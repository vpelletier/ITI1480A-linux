#include <fx2macros.h>
#include <delay.h>
#include <eputils.h>

#define SYNCDELAY SYNCDELAY3
#define CLKSPD48 bmCLKSPD1
#define IFCFGFIFO (bmIFCFG0 | bmIFCFG1)
#define bmSLCS bmBIT6
#define bmEP1OUTBSY bmBIT1
#define bmDYN_OUT bmBIT1
#define bmENH_PKT bmBIT0
#define TYPEBULK bmBIT5

#define FPGA_nCONFIG bmBIT7
#define FPGA_nSTATUS bmBIT6
#define FPGA_CONF_DONE bmBIT5
#define FPGA_DCLK bmBIT4

#define CONFIG_UNCONFIGURED 0
/* Device configuration compatible with ITI's firmware */
#define CONFIG_COMPATIBLE 1

#define COMMAND_FPGA 0
#define COMMAND_STOP 1
#define COMMAND_STATUS 2
#define COMMAND_PAUSE 3

#define COMMAND_FPGA_CONFIGURE_START 0
#define COMMAND_FPGA_CONFIGURE_WRITE 1
#define COMMAND_FPGA_CONFIGURE_STOP 2

volatile BYTE config;

//************************** Configuration Handlers *****************************

// change to support as many interfaces as you need
//volatile xdata BYTE interface=0;
//volatile xdata BYTE alt=0; // alt interface

// set *alt_ifc to the current alt interface for ifc
BOOL handle_get_interface(BYTE ifc, BYTE* alt_ifc) {
// *alt_ifc=alt;
    return TRUE;
}
// return TRUE if you set the interface requested
// NOTE this function should reconfigure and reset the endpoints
// according to the interface descriptors you provided.
BOOL handle_set_interface(BYTE ifc,BYTE alt_ifc) {
    //interface=ifc;
    //alt=alt_ifc;
    return TRUE;
}

BYTE handle_get_configuration(void) {
    return config;
}

BOOL handle_set_configuration(BYTE cfg) {
    /* When changing configuration, use internal clock so we can configure
    endpoints even if there is no clock on IFCLK input */
    IFCONFIG = bmIFCLKSRC | bm3048MHZ;
    SYNCDELAY;
    REVCTL = bmDYN_OUT | bmENH_PKT; SYNCDELAY;
    FIFORESET = bmNAKALL; SYNCDELAY;
    FIFORESET = bmNAKALL | 2; SYNCDELAY;
    FIFORESET = bmNAKALL | 4; SYNCDELAY;
    FIFORESET = bmNAKALL | 6; SYNCDELAY;
    FIFORESET = bmNAKALL | 8; SYNCDELAY;
    switch (cfg) {
        case CONFIG_UNCONFIGURED:
            EP1OUTCFG &= ~bmVALID; SYNCDELAY;
            EP1INCFG &= ~bmVALID; SYNCDELAY;
            EP2CFG &= ~bmVALID; SYNCDELAY;
            EP4CFG &= ~bmVALID; SYNCDELAY;
            EP6CFG &= ~bmVALID; SYNCDELAY;
            EP8CFG &= ~bmVALID; SYNCDELAY;
            break;
        case CONFIG_COMPATIBLE:
            EP1OUTCFG = bmVALID | TYPEBULK; SYNCDELAY;
            EP1INCFG = bmVALID | TYPEBULK; SYNCDELAY;
            // XXX: syncdelay not required by spec, although required for
            // similar regs.
            EP1OUTBC = 0; SYNCDELAY;
            EP2CFG = bmVALID | bmDIR | TYPEBULK; SYNCDELAY;
            EP2FIFOCFG = bmAUTOIN | bmWORDWIDE; SYNCDELAY;
            /* Autocommit 512B packets */
            EP2AUTOINLENH = 2; SYNCDELAY;
            EP2AUTOINLENL = 0; SYNCDELAY;
            EP4CFG &= ~bmVALID; SYNCDELAY;
            EP6CFG &= ~bmVALID; SYNCDELAY;
            EP8CFG &= ~bmVALID; SYNCDELAY;
            PINFLAGSAB = 0; SYNCDELAY;
            PINFLAGSCD = 0; SYNCDELAY;
            FIFOPINPOLAR = 0; SYNCDELAY;
            break;
        default:
            return FALSE;
    }
    FIFORESET = 0; SYNCDELAY;
    config = cfg;
    return TRUE;
}

//******************* VENDOR COMMAND HANDLERS **************************

BOOL handle_vendorcommand(BYTE cmd) {
    return FALSE;
}

//********************  INIT ***********************

void main_init(void) {
    /* 1 CLKOUT: CLK0 23 */
    CPUCS = CLKSPD48 | bmCLKOE;
    /* Disable extra movx delays */
    CKCON &= ~(bmBIT2 | bmBIT1 | bmBIT0);

    /* PortA pinout:
    INT0: TP14, 133
    PA1: TP4, 119
    SLOE: VCC
    PA3: D1 ("Host power") led, then R7 and VCC - so on when low.
    FIFOADR0:
    FIFOADR1:
    PKTEND:
    SLCS#: GND
    */
    PORTACFG = bmSLCS | bmINT0;
    IOA = bmBIT1;
    OEA = bmBIT3 | bmBIT1;

    /* PortE pinout:
    108 PE0:             114
    109 PE1:             113
    110 PE2:             112
    Used to load FPGA bitstream:
    111 RXD0OUT:   DATA0  20
    112 PE4 & TXD0: DCLK  21
    113 PE5:   CONF_DONE 123
    114 PE6:     STATUS# 121
    115 PE7:     CONFIG#  26
    */
    PORTECFG = bmRXD0OUT;
    IOE = bmBIT2 | bmBIT1 | bmBIT0;
    OEE = FPGA_nCONFIG | FPGA_DCLK | bmBIT2 | bmBIT1 | bmBIT0;
    /* SCON0 = XXXXX100: CLKOUT / 4, mode 0 */
    SM2 = 1;

    handle_set_configuration(CONFIG_UNCONFIGURED);
}

inline void FPGAConfigureStart(void) {
    /* Put FPGA into reset stage. */
    /* Pull nCONFIG down */
    IOE &= ~FPGA_nCONFIG;
    /* Pull PE4 up to allow TXD0 signal to reach DCLK */
    IOE |= FPGA_DCLK;
    /* Wait for nSTATUS to become low */
    while (IOE & FPGA_nSTATUS);
    /* Pull nCONFIG up */
    IOE |= FPGA_nCONFIG;
    /* Arm TI to simulate a previously-completed transfer. */
    TI = 1;
    /* Wait for nSTATUS to become high */
    while (!(IOE & FPGA_nSTATUS));
}

__sbit __at 0x98+1 TI_clear;
inline BOOL FPGAConfigureWrite(__xdata unsigned char *buf, unsigned char len) {
    /* Send len bytes from buf to FPGA. */
    __idata unsigned char preloaded;
    while (len) {
        /* Do as much as possible before checking TI, to do something useful
        instead of (maybe) just polling TI. */
        len--;
        preloaded = *buf++;
        while (!TI);
        /* Clear TI under an alias, so compiler doesn't think we are intending
        to use a semaphore. Atomic "jbc-sjmp" is slower than "jnb-clr". */
        TI_clear = 0;
        SBUF0 = preloaded;
    }
    return !(IOE & FPGA_nSTATUS);
}

inline void FPGAConfigureStop(void) {
    /* XXX: doesn't ensure the init stage is over.
    INIT_DONE pin is attached to PD6/FD4. Maybe it
    can be used ?
    If FPGA uses internal 10MHz clock for init, it
    takes 29.9us to finish configuration (and it
    probably does, CLKUSR doesn't seem connected to
    anything). */
    IOA &= ~bmBIT1;
    /* PortB pinout: FD[7:0]
       PortD pinout: FD[15:8]
    */
    IFCONFIG = IFCFGFIFO;
    /* XXX: assuming output clock is 48MHz */
    SYNCDELAY; RESETFIFOS();
    IOA |= bmBIT1;
}

inline void outPortC(unsigned char value, unsigned char ioe_mask) {
    IOC = value;
    OEC = 0xff;
    IOE &= ~ioe_mask;
    IOE |= ioe_mask;
    OEC = 0;
}

inline unsigned char inPortC(unsigned char ioe_mask) {
    unsigned char result;
    IOE &= ~ioe_mask;
    result = IOC;
    IOE |= ioe_mask;
    return result;
}

inline unsigned char FPGACommandRecv(void) {
    outPortC(0x80, bmBIT0);
    return inPortC(bmBIT1);
}

inline void FPGACommandSend(unsigned char command) {
    outPortC(0, bmBIT0);
    outPortC(command, bmBIT2);
}

inline void CommandPause(BYTE arg) {
    FPGACommandSend(arg ? 2 : 0);
}

inline void CommandStop(void) {
    FPGACommandSend(1);
}

inline BYTE CommandStatus() {
    return FPGACommandRecv();
}

inline void compatible_main_loop(void) {
    if (!(EP01STAT & bmEP1OUTBSY)) {
        if (EP1OUTBC == 64) {
            switch (EP1OUTBUF[0]) {
                case COMMAND_FPGA:
                    switch (EP1OUTBUF[1]) {
                        case COMMAND_FPGA_CONFIGURE_START:
                            FPGAConfigureStart();
                            break;
                        case COMMAND_FPGA_CONFIGURE_WRITE:
                            if (FPGAConfigureWrite(EP1OUTBUF + 2, EP1OUTBUF[63])) {
                                EP1OUTCS |= bmEPSTALL;
                            }
                            break;
                        case COMMAND_FPGA_CONFIGURE_STOP:
                            FPGAConfigureStop();
                            break;
                        default:
                            EP1OUTCS |= bmEPSTALL;
                            break;
                    }
                    break;
                case COMMAND_STOP:
                    CommandStop();
                    break;
                case COMMAND_STATUS:
                    EP1INBUF[0] = 2;
                    EP1INBUF[1] = CommandStatus();
                    SYNCDELAY; EP1INBC = 64;
                    break;
                case COMMAND_PAUSE:
                    CommandPause(EP1OUTBUF[1]);
                    break;
                default:
                    EP1OUTCS |= bmEPSTALL;
                    break;
            }
        }
        SYNCDELAY;
        EP1OUTBC = 0; SYNCDELAY;
    }
}

void main_loop(void) {
    switch(config) {
        case CONFIG_COMPATIBLE:
            compatible_main_loop();
            break;
    }
}
