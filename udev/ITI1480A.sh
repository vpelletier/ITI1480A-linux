#!/bin/sh

BUSNUM="$1"
DEVNUM="$2"
FIRMWARE="$3"
DEVICE=`printf "%03i.%03i" "$BUSNUM" "$DEVNUM"`
cycfx2prog -d="$DEVICE" "prg:${FIRMWARE}" run

