SOURCES=fw.c device.c
A51_SOURCES=dscr.a51
BASENAME=ITI1480A
SDCCFLAGS=--std-c99

VID=0x16C0
PID=0x07A8

include $(FX2LIBDIR)/lib/fx2.mk

load-cycfx2prog: $(BUILDDIR)/$(BASENAME).ihx
	cycfx2prog -id="$(VID).$(PID)" "prg:$(BUILDDIR)/$(BASENAME).ihx" run

# XXX: how portable is this ? Debian (& derived) only ? udev-based only ?
#install: $(BUILDDIR)/$(BASENAME).ihx
#	install -T "$(BUILDDIR)/$(BASENAME).ihx" /lib/firmware/ITI1480A.ihx
