#!/usr/bin/env python
import os
import wx
import threading
import signal
import subprocess

from gui import wxITI1480AMainFrame
from iti1480a.parser import tic_to_time, short_tic_to_time, tic_to_us, Parser, ReorderedStream, MESSAGE_RAW, MESSAGE_RESET, MESSAGE_TRANSACTION, MESSAGE_SOF

def tic_to_s(tic):
    return tic_to_us(tic) / 1000

class Capture(object):
    _subprocess = None
    _open_thread = None
    paused = False

    def __init__(self, callback):
        self._callback = callback

    def start(self):
        self._subprocess = capture = subprocess.Popen([
            '../capture.py', '-f', '/lib/firmware/ITI1480A.rbf', '-v'],
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

CHUNK_SIZE = 16 * 1024
class ITI1480AMainFrame(wxITI1480AMainFrame):
    _last_tic = None
    _statusbar_size_changed = False

    def __init__(self, *args, **kw):
        super(ITI1480AMainFrame, self).__init__(*args, **kw)
        self._openDialog = wx.FileDialog(self, 'Choose a file', '', '', '*.usb', wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        tree = self.tree_list
        for column_id, (column_name, width) in enumerate([
                    ('Item', 170),
                    ('Device', 40),
                    ('Endpoint', 40),
                    ('Interface', 40),
                    ('Status', 40),
                    ('Speed', 40),
                    ('Payload', 300),
                    ('Time (min:sec.ms\'us"ns)', 140),
                    ('Time (min:sec.ms\'us"ns)', 140),
                ]):
            tree.AddColumn(column_name, width=width)
        self._delta_time_id = column_id # Absolute time is delta-1
        self._onTimestamp(True)
        tree.SetMainColumn(0)
        tree.AddRoot('')
        image_size = (16, 16)
        self.image_list = image_list = wx.ImageList(*image_size)
        self._folderClosed = image_list.Add(wx.ArtProvider_GetBitmap(wx.ART_FOLDER, wx.ART_OTHER, image_size))
        self._folderOpened = image_list.Add(wx.ArtProvider_GetBitmap(wx.ART_FILE_OPEN, wx.ART_OTHER, image_size))
        self._file = image_list.Add(wx.ArtProvider_GetBitmap(wx.ART_NORMAL_FILE, wx.ART_OTHER, image_size))
        tree.SetImageList(image_list)
        self.load_gauge = gauge = wx.Gauge(self.statusbar, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
        gauge.Show(False)
        self.statusbar.Bind(wx.EVT_SIZE, self.onResizeStatusbar)
        self.statusbar.Bind(wx.EVT_IDLE, self.onIdleStatusbar)
        self._repositionGauge()
        self._capture = Capture(self._openFile)

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

    def onAbsoluteTimestamp(self, event):
        self._onTimestamp(True)

    def onRelativeTimestamp(self, event):
        self._onTimestamp(False)

    def _onTimestamp(self, absolute):
        SetColumnShown = self.tree_list.SetColumnShown
        SetColumnShown(self._delta_time_id - 1, shown=absolute)
        SetColumnShown(self._delta_time_id, shown=not absolute)

    def onOpen(self, event):
        dialog = self._openDialog
        if dialog.ShowModal() == wx.ID_OK:
            stream = open(dialog.GetPath())
            gauge = self.load_gauge
            gauge.SetValue(0)
            stream.seek(0, 2)
            gauge.SetRange(stream.tell())
            stream.seek(0)
            gauge.Show(True)
            open_thread = threading.Thread(target=self._openFile, args=(stream.read, ), kwargs={'use_gauge': True})
            open_thread.daemon = True
            open_thread.start()

    def _openFile(self, read, use_gauge=False, read_buf=CHUNK_SIZE):
        pending_sof = []
        pending_nak = []
        def render_transaction(tic, data):
            start, payload, stop, stop_tic = data
            if payload is None:
                readable = ''
            else:
                readable = ' '.join('%02x' % (ord(x), ) for x in payload['data'])
            return (
                start['name'],
                [str(start['address']), str(start['endpoint']), '', stop['name'], '', readable],
                tic, [
                    (start['name'], [str(start['address']), str(start['endpoint']), ''], tic, []),
                    (stop['name'], [str(start['address']), str(start['endpoint']), ''], stop_tic, []),
                ],
            )

        def transaction(tic, data):
            start, _, stop, _ = data
            if stop is not None and stop['name'] == 'NAK' and (not pending_nak or pending_nak[0][1][0] == start):
                pending_nak.append((tic, data))
                return
            elif pending_nak:
                count = len(pending_nak)
                nak_tic, nak_data = pending_nak[0]
                if count == 1:
                    transaction(nak_tic, nak_data)
                else:
                    addBaseTreeItem(
                        '%s (x%i)' % (nak_data[0]['name'], count),
                        (str(nak_data[0]['address']), str(nak_data[0]['endpoint']), '', 'NAK', '', '', ''),
                        nak_tic,
                        [render_transaction(x[0], x[1]) for x in pending_nak],
                    )
                del pending_nak[:]
            addBaseTreeItem(*render_transaction(tic, data))

        # TODO: add support for more types.
        dispatch = {
            MESSAGE_TRANSACTION: transaction,
        }

        def addTreeItem(parent, caption, data, absolute_tic, previous_tic, child_list):
            tree_item = AppendItem(parent, caption)
            for column, caption in enumerate(data, 1):
                SetItemText(tree_item, caption, column)
            SetItemText(tree_item, tic_to_time(absolute_tic), self._delta_time_id - 1)
            if previous_tic is not None:
                SetItemText(tree_item, tic_to_time(absolute_tic - previous_tic), self._delta_time_id)
            previous_child_tic = previous_tic
            if child_list:
                SetItemImage(tree_item, self._folderClosed, which=wx.TreeItemIcon_Normal)
                SetItemImage(tree_item, self._folderOpened, which=wx.TreeItemIcon_Expanded)
            else:
                SetItemImage(tree_item, self._file, which=wx.TreeItemIcon_Normal)
            for child_caption, child_data, child_absolute_tic, grand_child_list in child_list:
                addTreeItem(tree_item, child_caption, child_data, child_absolute_tic, previous_child_tic, grand_child_list)
                previous_child_tic = child_absolute_tic

        def addBaseTreeItem(caption, data, tic, child_list):
            wx.MutexGuiEnter()
            try:
                addTreeItem(root, caption, data, tic, self._last_tic, child_list)
            finally:
                wx.MutexGuiLeave()
            self._last_tic = tic

        def push(tic, message_type, data):
            if message_type == MESSAGE_SOF:
                pending_sof.append((data['frame'], tic))
            else:
                if pending_sof:
                    if len(pending_sof) == 1:
                        addBaseTreeItem('Start of frame %i' % (pending_sof[0][0], ), [], pending_sof[0][1], [])
                    else:
                        addBaseTreeItem(
                            'SOF (x%i, %i to %i)' % (
                                len(pending_sof),
                                pending_sof[0][0],
                                pending_sof[-1][0]),
                            [],
                            pending_sof[0][1], [
                                ('Start of frame %i' % (x, ), [], y, []) for x, y in pending_sof
                            ]
                        )
                    del pending_sof[:]
                try:
                    renderer = dispatch[message_type]
                except KeyError:
                    return
                renderer(tic, data)

        self._last_tic = None
        tree = self.tree_list
        root = tree.GetRootItem()
        tree.DeleteChildren(root)
        AppendItem = tree.AppendItem
        SetItemText = tree.SetItemText
        read_length = 0
        parse = ReorderedStream(Parser(push)).push
        SetItemImage = tree.SetItemImage
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
            # XXX: shoud use a separate parameter
            if parse(data) or (use_gauge and len(data) < CHUNK_SIZE):
                break
        if use_gauge:
            wx.MutexGuiEnter()
            try:
                gauge.Show(False)
            finally:
                wx.MutexGuiLeave()

def main():
    app = wx.PySimpleApp(0)
    wx.InitAllImageHandlers()
    main_frame = ITI1480AMainFrame(None, -1, "")
    app.SetTopWindow(main_frame)
    main_frame.Show()
    app.MainLoop()

if __name__ == '__main__':
    main()

