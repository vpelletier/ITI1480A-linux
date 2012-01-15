#!/usr/bin/env python
import os
import wx
import threading
import signal
import subprocess

# TODO: use wxWidget 2.9 wxTreeCtrl
from wx.gizmos import TreeListCtrl

from gui import wxITI1480AMainFrame
from iti1480a.parser import tic_to_time, short_tic_to_time, \
    ReorderedStream, MESSAGE_RAW, MESSAGE_RESET, MESSAGE_TRANSACTION, \
    decode, TOKEN_TYPE_ACK, TOKEN_TYPE_NAK, TOKEN_TYPE_STALL, \
    TOKEN_TYPE_NYET, Packetiser, TransactionAggregator, PipeAggregator, \
    Endpoint0TransferAggregator, MESSAGE_TRANSFER, ParsingDone

class Capture(object):
    _subprocess = None
    _open_thread = None
    paused = False

    def __init__(self, callback):
        self._callback = callback

    def start(self):
        # TODO: unhardcode paths and make them portable.
        # Maybe import capture and run its entry point directly...
        # Anyway, the whole way this class works needs a (re)think.
        # ...When GUI works fine, that is.
        self._subprocess = capture = subprocess.Popen([
            '../iti1480a/capture.py', '-f', '/lib/firmware/ITI1480A.rbf', '-v'],
            stdout=subprocess.PIPE,
        )
        self._open_thread = read_thread = threading.Thread(
            target=self._callback,
            args=(capture.stdout.read, ), kwargs={'read_buf': 16})
        read_thread.daemon = True
        read_thread.start()

    def pause(self):
        self.paused = True
        self._subprocess.send_signal(signal.SIGTSTP)

    def cont(self):
        self._subprocess.send_signal(signal.SIGCONT)
        self._paused = False

    def stop(self):
        self._subprocess.kill()
        self._subprocess.wait()
        self._open_thread = self._subprocess = None

class EventListManagerBase(object):
    # XXX: horrible API
    def __init__(self, event_list, addBaseTreeItemList):
        self._event_list = event_list
        self.__addBaseTreeItemList = addBaseTreeItemList
        #self._tree_buf = []

    def _addBaseTreeItem(self, *args, **kw):
        self.__addBaseTreeItemList(self._event_list, ((args, kw), ))
        #tree_buf = self._tree_buf
        #tree_buf.append((args, kw))
        # XXX: bad for live capture
        #if len(tree_buf) > 50:
        #    self._flush()

    #def _flush(self):
        #self.__addBaseTreeItemList(self._event_list, self._tree_buf)
        #self._tree_buf = []

    def push(self, tic, transaction_type, data):
        raise NotImplementedError

    def stop(self):
        pass
        #self._flush()

class HubEventListManager(EventListManagerBase):
    def push(self, tic, transaction_type, data):
        pass

class EndpointEventListManager(EventListManagerBase):
    def push(self, tic, transaction_type, data):
        if transaction_type == MESSAGE_TRANSFER:
            _decode = self._decode
            child_list = []
            append = child_list.append
            for _, packets in data:
                append(_decode(packets))
        elif transaction_type == MESSAGE_TRANSACTION:
            child_list = [self._decode(data)]
        first_child = child_list[0]
        device, endpoint, interface, _, speed, payload = first_child[1]
        status = child_list[-1][1][3]
        self._addBaseTreeItem(first_child[0], (device, endpoint, interface, status, speed, payload), first_child[2], child_list)

    def _decode(self, packets):
        decoded = [decode(x) for x in packets]
        start = decoded[0]
        interface = '' # TODO
        handshake = decoded[-1]
        if handshake['name'] in (TOKEN_TYPE_ACK, TOKEN_TYPE_NAK,
                TOKEN_TYPE_STALL, TOKEN_TYPE_NYET):
            status = handshake['name']
        else:
            status = ''
        speed = '' # TODO (LS/FS/HS)
        payload = ''
        for item in decoded:
            if 'data' in item:
                payload += (' '.join('%02x' % (ord(x), )
                    for x in item['data']))
        return (start['name'], (str(start['address']), str(
            start['endpoint']), interface, status, speed, payload), start['tic'], (
            (x['name'], ('', '', '', '', '',
                ' '.join('%02x' % (ord(y), ) for y in x.get('data', ''))
                ), x['tic'], ()) for x in decoded
        ))

CHUNK_SIZE = 16 * 1024
class ITI1480AMainFrame(wxITI1480AMainFrame):
    _statusbar_size_changed = False

    def __init__(self, *args, **kw):
        loadfile = kw.pop('loadfile', None)
        cwd = os.getcwd()
        os.chdir(os.path.dirname(__file__))
        super(ITI1480AMainFrame, self).__init__(*args, **kw)
        os.chdir(cwd)
        self._openDialog = wx.FileDialog(self, 'Choose a file', '', '',
            '*.usb', wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        image_size = (16, 16)
        self.image_list = image_list = wx.ImageList(*image_size)
        self._folderClosed = image_list.Add(wx.ArtProvider_GetBitmap(
            wx.ART_FOLDER, wx.ART_OTHER, image_size))
        self._folderOpened = image_list.Add(wx.ArtProvider_GetBitmap(
            wx.ART_FILE_OPEN, wx.ART_OTHER, image_size))
        self._file = image_list.Add(wx.ArtProvider_GetBitmap(
            wx.ART_NORMAL_FILE, wx.ART_OTHER, image_size))
        self.load_gauge = gauge = wx.Gauge(self.statusbar,
            style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
        gauge.Show(False)
        self.statusbar.Bind(wx.EVT_SIZE, self.onResizeStatusbar)
        self.statusbar.Bind(wx.EVT_IDLE, self.onIdleStatusbar)
        self._repositionGauge()
        self._capture = Capture(self._openFile)
        self._device_dict = {}
        self._initEventList(self.capture_list)
        self._initEventList(self.bus_list)
        if loadfile is not None:
            self.openFile(loadfile)

    def _getHubEventList(self, device):
        try:
            event_list, = self._device_dict[device]
        except KeyError:
            event_list = self._newEventList(self.device_notebook)
            self._device_dict[endpoint] = (event_list, )
            self.device_notebook.AddPage(event_list, 'Hub %i' % (device, ))
        assert not isinstance(event_list, dict), (device, event_list)
        return event_list

    def _getEndpointEventList(self, device, endpoint):
        try:
            endpoint_notebook, endpoint_dict = self._device_dict[device]
        except KeyError:
            create_device = True
            endpoint_dict = {}
            endpoint_notebook = wx.Notebook(self.device_notebook, -1, style=0)
            self._device_dict[device] = (endpoint_notebook, endpoint_dict)
        else:
            create_device = False
        try:
            event_list = endpoint_dict[endpoint]
        except KeyError:
            endpoint_dict[endpoint] = event_list = self._newEventList(endpoint_notebook)
            endpoint_notebook.AddPage(event_list, 'Ep %i' % (endpoint, ))
            if create_device:
                self.device_notebook.AddPage(
                    endpoint_notebook,
                    'Device %i' % (device, ),
                )
        return event_list

    def _initEventList(self, tree):
        for column_id, (column_name, width) in enumerate([
                    ('Time (min:sec.ms\'us"ns)', 140),
                    ('Item', 170),
                    ('Device', 40),
                    ('Endpoint', 40),
                    ('Interface', 40),
                    ('Status', 40),
                    ('Speed', 40),
                    ('Payload', 300),
                ]):
            tree.AddColumn(column_name, width=width)
        tree.SetMainColumn(1)
        tree.AddRoot('')
        tree.SetImageList(self.image_list)

    def _newEventList(self, parent):
        tree = TreeListCtrl(parent, -1, style=wx.TR_HIDE_ROOT | wx.TR_NO_BUTTONS | wx.TR_ROW_LINES | wx.TR_FULL_ROW_HIGHLIGHT)
        self._initEventList(tree)
        return tree

    def _repositionGauge(self):
        rect = self.statusbar.GetFieldRect(1)
        self.load_gauge.SetPosition((rect.x+2, rect.y+2))
        self.load_gauge.SetSize((rect.width-4, rect.height-4))

    def onResizeStatusbar(self, event):
        # XXX: see description of this trick in the wx StatusBar demo.
        self._statusbar_size_changed = True
        self._repositionGauge()

    def onIdleStatusbar(self, event):
        if self._statusbar_size_changed:
            self._repositionGauge()
            self._statusbar_size_changed = False

    def onExit(self, event):
        self.Close(True)

    def onStart(self, event):
        self._capture.start()

    def onStop(self, event):
        self._capture.stop()

    def onPause(self, event):
        if self._capture.paused:
            self._capture.cont()
        else:
            self._capture.pause()

    def onOpen(self, event):
        dialog = self._openDialog
        if dialog.ShowModal() == wx.ID_OK:
            self.openFile(dialog.GetPath())

    def openFile(self, path):
        stream = open(path)
        gauge = self.load_gauge
        gauge.SetValue(0)
        stream.seek(0, 2)
        gauge.SetRange(stream.tell())
        stream.seek(0)
        gauge.Show(True)
        open_thread = threading.Thread(target=self._openFile,
            args=(stream.read, ), kwargs={'use_gauge': True})
        open_thread.daemon = True
        open_thread.start()

    def _openFile(self, read, use_gauge=False, read_buf=CHUNK_SIZE):
        def addTreeItem(event_list, parent, caption, data, absolute_tic,
                child_list):
            SetItemText = event_list.SetItemText
            SetItemImage = event_list.SetItemImage
            tree_item = event_list.AppendItem(parent, caption)
            SetItemText(tree_item, tic_to_time(absolute_tic), 0)
            for column, caption in enumerate(data, 2):
                SetItemText(tree_item, caption, column)
            if child_list:
                SetItemImage(tree_item, self._folderClosed,
                    which=wx.TreeItemIcon_Normal)
                SetItemImage(tree_item, self._folderOpened,
                    which=wx.TreeItemIcon_Expanded)
            else:
                SetItemImage(tree_item, self._file,
                    which=wx.TreeItemIcon_Normal)
            for (child_caption, child_data, child_absolute_tic,
                    grand_child_list) in child_list:
                addTreeItem(event_list, tree_item, child_caption, child_data,
                    child_absolute_tic, grand_child_list)

        def addBaseTreeItem(event_list, caption, data, tic, child_list):
            wx.MutexGuiEnter()
            try:
                addTreeItem(event_list, event_list.GetRootItem(), caption, data, tic,
                    child_list)
            finally:
                wx.MutexGuiLeave()

        def addBaseTreeItemList(event_list, arg_list):
            wx.MutexGuiEnter()
            try:
                root = event_list.GetRootItem()
                for args, kw in arg_list:
                    addTreeItem(event_list, root, *args, **kw)
            finally:
                wx.MutexGuiLeave()

        def captureEvent(tic, event_type, data):
            if event_type == MESSAGE_RAW:
                addBaseTreeItem(self.capture_list, data, (), tic, ())
            elif event_type == MESSAGE_RESET:
                addBaseTreeItem(self.capture_list, 'Reset (%s)' % (short_tic_to_time(data)), (), tic, ())
            else:
                raise NotImplementedError(event_type)
        captureEvent.stop = lambda: None
        captureEvent.push = captureEvent

        def busEvent(tic, event_type, data):
            assert event_type == MESSAGE_TRANSACTION, event_type
            assert len(data) == 1, data
            addBaseTreeItem(self.bus_list, 'SOF %i' % (decode(data[0])['frame'], ), (), tic, ())
        busEvent.stop = lambda: None
        busEvent.push = busEvent

        def newHub(address):
            wx.MutexGuiEnter()
            try:
                event_list = self._getHubEventList(address)
            finally:
                wx.MutexGuiLeave()
            return HubEventListManager(event_list, addBaseTreeItem)

        def newPipe(address, endpoint):
            wx.MutexGuiEnter()
            try:
                event_list = self._getEndpointEventList(address, endpoint)
            finally:
                wx.MutexGuiLeave()
            result = EndpointEventListManager(event_list, addBaseTreeItemList)
            if endpoint == 0:
                result = Endpoint0TransferAggregator(result, captureEvent)
            return result

        read_length = 0
        stream = ReorderedStream(
            Packetiser(
                TransactionAggregator(
                    PipeAggregator(
                        busEvent,
                        captureEvent,
                        newHub,
                        newPipe,
                    ),
                    captureEvent,
                ),
                captureEvent,
            )
        )
        parse = stream.push
        if use_gauge:
            gauge = self.load_gauge
            SetGaugeValue = gauge.SetValue
        while True:
            data = read(read_buf)
            if use_gauge:
                read_length += len(data)
                wx.MutexGuiEnter()
                try:
                    SetGaugeValue(read_length)
                    pass
                finally:
                    wx.MutexGuiLeave()
            try:
                parse(data)
            except ParsingDone:
                stream.stop()
                break
            # XXX: shoud use a separate parameter
            if use_gauge and len(data) < CHUNK_SIZE:
                break
        if use_gauge:
            wx.MutexGuiEnter()
            try:
                gauge.Show(False)
            finally:
                wx.MutexGuiLeave()

def main():
    import sys
    if len(sys.argv) == 2:
        loadfile = sys.argv[1]
    else:
        loadfile = None
    app = wx.PySimpleApp(0)
    wx.InitAllImageHandlers()
    main_frame = ITI1480AMainFrame(None, -1, "", loadfile=loadfile)
    app.SetTopWindow(main_frame)
    main_frame.Show()
    app.MainLoop()

if __name__ == '__main__':
    main()

