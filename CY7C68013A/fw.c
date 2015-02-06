/**
 * Copyright (C) 2009 Ubixum, Inc.
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation; either
 * version 2.1 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public
 * License along with this library; if not, write to the Free Software
 * Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301  USA
 **/

#include <fx2macros.h>
#include <fx2ints.h>
#include <autovector.h>
#include <delay.h>
#include <setupdat.h>

#define ENABLE_EP0OUT() EPIE|=bmEP0OUT
#define CLEAR_EP0OUT() CLEAR_USBINT(); EPIRQ=bmEP0OUT

volatile __bit dosud=FALSE;
volatile __bit dosuspend=FALSE;
volatile __bit doep0out=FALSE;

// custom functions
extern void main_loop();
extern void main_init();
extern void handle_ep0_out();

void main() {
 main_init();

 // set up interrupts.
 USE_USB_INTS();

 ENABLE_SUDAV();
 ENABLE_USBRESET();
 ENABLE_HISPEED();
 ENABLE_SUSPEND();
 ENABLE_RESUME();
 NAKIRQ = bmIBN;
 NAKIE |= bmIBN;
 IBNIRQ = 0xff;
 IBNIE |= bmEP2IBN;
 ENABLE_EP0OUT();

 EA=1;

// iic files (c2 load) don't need to renumerate/delay
// trm 3.6
#ifndef NORENUM
 RENUMERATE();
#else
 USBCS &= ~bmDISCON;
#endif

 while(TRUE) {

     main_loop();

     if (dosud) {
       dosud=FALSE;
       handle_setupdata();
     }

     if (doep0out) {
       doep0out=FALSE;
       handle_ep0_out();
     }

     if (dosuspend) {
        dosuspend=FALSE;
        do {
           WAKEUPCS |= bmWU|bmWU2; // make sure ext wakeups are cleared
           SUSPEND=1;
           PCON |= 1;
           __asm
           nop
           nop
           nop
           nop
           nop
           nop
           nop
           __endasm;
        } while ( !remote_wakeup_allowed && REMOTE_WAKEUP());
        // resume
        // trm 6.4
        if ( REMOTE_WAKEUP() ) {
            delay(5);
            USBCS |= bmSIGRESUME;
            delay(15);
            USBCS &= ~bmSIGRESUME;
        }

     }

 } // end while

} // end main

void resume_isr() __interrupt RESUME_ISR {
 CLEAR_RESUME();
}

void sudav_isr() __interrupt SUDAV_ISR {
 dosud=TRUE;
 CLEAR_SUDAV();
}
void usbreset_isr() __interrupt USBRESET_ISR {
 handle_hispeed(FALSE);
 CLEAR_USBRESET();
}
void hispeed_isr() __interrupt HISPEED_ISR {
 handle_hispeed(TRUE);
 CLEAR_HISPEED();
}

void suspend_isr() __interrupt SUSPEND_ISR {
 dosuspend=TRUE;
 CLEAR_SUSPEND();
}

void ep0out_isr() __interrupt EP0OUT_ISR {
 doep0out=TRUE;
 CLEAR_EP0OUT();
}
