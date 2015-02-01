SOURCES=fw.c device.c
A51_SOURCES=dscr.a51
BASENAME=ITI1480A
SDCCFLAGS=--std-c99
DSCR_AREA=-Wl"-b DSCR_AREA=0x3d00"

VID=0x16C0
PID=0x07A8
PID2=0x07A9

include $(FX2LIBDIR)/lib/fx2.mk

reload-cycfx2prog: $(BUILDDIR)/$(BASENAME).ihx
	cycfx2prog -id="$(VID).$(PID2)" "prg:$(BUILDDIR)/$(BASENAME).ihx" run

load-cycfx2prog: $(BUILDDIR)/$(BASENAME).ihx
	cycfx2prog -id="$(VID).$(PID)" "prg:$(BUILDDIR)/$(BASENAME).ihx" run

install: $(BUILDDIR)/$(BASENAME).ihx
	install -T "$(BUILDDIR)/$(BASENAME).ihx" /lib/firmware/ITI1480A.ihx
