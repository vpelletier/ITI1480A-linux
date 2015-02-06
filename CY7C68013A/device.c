#include <autovector.h>
#include <fx2macros.h>
#include <delay.h>
#include <eputils.h>

#define SYNCDELAY SYNCDELAY3
#define CLKSPD48 bmCLKSPD1
#define bmSLCS bmBIT6
#define bmEP1OUTBSY bmBIT1
#define TYPEBULK bmBIT5
#define RESETFIFOS_START() do { \
    FIFORESET=bmNAKALL; SYNCDELAY;\
    FIFORESET=bmNAKALL | 2; SYNCDELAY;\
    FIFORESET=bmNAKALL | 4; SYNCDELAY;\
    FIFORESET=bmNAKALL | 6; SYNCDELAY;\
    FIFORESET=bmNAKALL | 8; SYNCDELAY;\
} while (0)
#define RESETFIFOS_STOP() do { FIFORESET=0; SYNCDELAY; } while (0)
#ifdef RESETFIFOS
  /* Some versions of RESETFIFOS do not keep bmNAKALL asserted when reseting
     individual fifos. */
  #undef RESETFIFOS
#endif
#define RESETFIFOS() {RESETFIFOS_START(); RESETFIFOS_STOP();}

/* bmRequestType field masks & values */
/* direction */
#define bmREQUESTTYPE_DIRECTION   0x80
#define REQUESTTYPE_DIRECTION_IN  0x80
#define REQUESTTYPE_DIRECTION_OUT 0x00
/* type */
#define bmREQUESTTYPE_TYPE        0x60
#define REQUESTTYPE_TYPE_STANDARD 0x00
#define REQUESTTYPE_TYPE_CLASS    0x20
#define REQUESTTYPE_TYPE_VENDOR   0x40
/* recipient */
#define bmREQUESTTYPE_RECIPIENT         0x1f
#define REQUESTTYPE_RECIPIENT_DEVICE    0x00
#define REQUESTTYPE_RECIPIENT_INTERFACE 0x01
#define REQUESTTYPE_RECIPIENT_ENDPOINT  0x02
#define REQUESTTYPE_RECIPIENT_OTHER     0x03

#define FPGA_nCONFIG bmBIT7
#define FPGA_nSTATUS bmBIT6
#define FPGA_CONF_DONE bmBIT5
#define FPGA_DCLK bmBIT4

#define CONFIG_UNCONFIGURED 0
#define CONFIG_CONFIGURED 1

#define VENDOR_COMMAND 0x10

#define COMMAND_FPGA 0
#define COMMAND_STOP 1
#define COMMAND_STATUS 2
#define COMMAND_PAUSE 3

#define COMMAND_FPGA_CONFIGURE_START 0
#define COMMAND_FPGA_CONFIGURE_WRITE 1
#define COMMAND_FPGA_CONFIGURE_STOP 2

static BYTE config = CONFIG_UNCONFIGURED;
static __bit fpga_configure_running = FALSE;
static WORD fpga_configure_to_receive = 0;

//************************** Configuration Handlers *****************************
BOOL handle_get_descriptor() {
    return FALSE;
}

BOOL handle_get_interface(BYTE ifc, BYTE* alt_ifc) {
    *alt_ifc = 0;
    return ifc == 0;
}

BOOL handle_set_interface(BYTE ifc,BYTE alt_ifc) {
    return ifc == 0 && alt_ifc == 0;
}

BYTE handle_get_configuration(void) {
    return config;
}

BOOL handle_set_configuration(BYTE cfg) {
    /* When changing configuration, use internal clock so we can configure
    endpoints even if there is no clock on IFCLK input */
    IFCONFIG |= bmIFCLKSRC; SYNCDELAY;
    /* Keep NAK running until FIFOs are fully configured */
    RESETFIFOS_START();
    switch (cfg) {
        case CONFIG_UNCONFIGURED:
            EP1OUTCFG &= ~bmVALID; SYNCDELAY;
            EP1INCFG &= ~bmVALID; SYNCDELAY;
            EP2CFG &= ~bmVALID; SYNCDELAY;
            EP4CFG &= ~bmVALID; SYNCDELAY;
            EP6CFG &= ~bmVALID; SYNCDELAY;
            EP8CFG &= ~bmVALID; SYNCDELAY;
            break;
        case CONFIG_CONFIGURED:
            EP1OUTCFG &= ~bmVALID; SYNCDELAY;
            EP1INCFG &= ~bmVALID; SYNCDELAY;
            EP2CFG = bmVALID | bmDIR | TYPEBULK; SYNCDELAY;
            EP2FIFOCFG = bmAUTOIN | bmWORDWIDE; SYNCDELAY;
            /* Autocommit 512B packets */
            EP2AUTOINLENH = 2; SYNCDELAY;
            EP2AUTOINLENL = 0; SYNCDELAY;
            EP4CFG &= ~bmVALID; SYNCDELAY;
            EP6CFG &= ~bmVALID; SYNCDELAY;
            EP8CFG &= ~bmVALID; SYNCDELAY;
            break;
        default:
            return FALSE;
    }
    PINFLAGSAB = 0; SYNCDELAY;
    PINFLAGSCD = 0; SYNCDELAY;
    FIFOPINPOLAR = 0; SYNCDELAY;
    config = cfg;
    RESETFIFOS_STOP();
    return TRUE;
}

//********************  INIT ***********************

void main_init(void) {
    /* Disable extra movx delays */
    CKCON &= ~(bmBIT2 | bmBIT1 | bmBIT0);
    /* Setup FIFO before CPUCS:
       - Use internal clock source as FPGA is not providing one yet
       - Set internal clock to 48MHz
       - Keep clock out disabled
       - Do not inverse clock polarity
       - Keep FIFO synchronous
       - Do not enable GSTATE
       - Set ports B and D as 16bits slave FIFO
    */
    IFCONFIG = bmIFCLKSRC | bm3048MHZ | bmIFCFG1 | bmIFCFG0; SYNCDELAY;
    /* 1 CLKOUT: CLK0 23 */
    CPUCS = CLKSPD48 | bmCLKOE;
    REVCTL = bmNOAUTOARM | bmSKIPCOMMIT; SYNCDELAY;

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

    EP0BCH = 0; SYNCDELAY; /* As of TRM rev.*D 8.6.1.2 */
    handle_set_configuration(CONFIG_UNCONFIGURED);
}

static inline void FPGAConfigureStart(void) {
    /* Switch to internal clock as FPGA will stop feeding IFCLK */
    IFCONFIG |= bmIFCLKSRC; SYNCDELAY4;
    /* Put FPGA into reset stage. */
    /* Pull nCONFIG down */
    IOE &= ~FPGA_nCONFIG;
    /* Pull PE4 up to allow TXD0 signal to reach DCLK */
    IOE |= FPGA_DCLK;

    /* Empty fifo and (re)enable AUTOIN. */
    FIFORESET = bmNAKALL; SYNCDELAY;
    EP2FIFOCFG &= ~bmAUTOIN; SYNCDELAY;
    FIFORESET = bmNAKALL | 2; SYNCDELAY;
    EP2FIFOCFG |= bmAUTOIN; SYNCDELAY;
    FIFORESET = 0; SYNCDELAY;

    /* Wait for nSTATUS to become low */
    while (IOE & FPGA_nSTATUS);
    /* Pull nCONFIG up */
    IOE |= FPGA_nCONFIG;
    /* Arm TI to simulate a previously-completed transfer. */
    TI = 1;
    /* Wait for nSTATUS to become high */
    while (!(IOE & FPGA_nSTATUS));
}

static __sbit __at 0x98+1 TI_clear;
static inline BOOL FPGAConfigureWrite(__xdata unsigned char *buf, unsigned char len) {
    /* Send len bytes from buf to FPGA. */
    unsigned char preloaded;
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

static inline void FPGAConfigureStop(void) {
    /* Empty fifo and (re)enable AUTOIN. */
    FIFORESET = bmNAKALL; SYNCDELAY;
    EP2FIFOCFG &= ~bmAUTOIN; SYNCDELAY;
    FIFORESET = bmNAKALL | 2; SYNCDELAY;
    EP2FIFOCFG |= bmAUTOIN; SYNCDELAY;
    FIFORESET = 0; SYNCDELAY;
    /* Switch FIFO clock source to external */
    IFCONFIG &= ~bmIFCLKSRC;
    IOA &= ~bmBIT1;
    /* PortB pinout: FD[7:0]
       PortD pinout: FD[15:8]
    */
    IOA |= bmBIT1;
}

static inline void outPortC(unsigned char value, unsigned char ioe_mask) {
    IOC = value;
    OEC = 0xff;
    IOE &= ~ioe_mask;
    IOE |= ioe_mask;
    OEC = 0;
}

static inline unsigned char inPortC(unsigned char ioe_mask) {
    unsigned char result;
    IOE &= ~ioe_mask;
    result = IOC;
    IOE |= ioe_mask;
    return result;
}

static inline unsigned char FPGACommandRecv(void) {
    outPortC(0x80, bmBIT0);
    return inPortC(bmBIT1);
}

static inline void FPGACommandSend(unsigned char command) {
    outPortC(0, bmBIT0);
    outPortC(command, bmBIT2);
}

static inline void CommandPause(BYTE arg) {
    FPGACommandSend(arg ? 2 : 0);
}

static inline void CommandStop(void) {
    FPGACommandSend(1);
}

static inline BYTE CommandStatus() {
    return FPGACommandRecv();
}

void main_loop(void) {
}

BOOL handle_vendorcommand(BYTE cmd) {
    BYTE request_type = SETUPDAT[0];
    BYTE subcommand = SETUPDAT[2];
    BYTE command = SETUPDAT[3];
    BOOL direction_in = (request_type & bmREQUESTTYPE_DIRECTION) ==
        REQUESTTYPE_DIRECTION_IN;
    WORD data_length = ((WORD *) SETUPDAT)[3];
    if (config != CONFIG_CONFIGURED || cmd != VENDOR_COMMAND ||
            (request_type & (bmREQUESTTYPE_TYPE | bmREQUESTTYPE_RECIPIENT)) !=
                (REQUESTTYPE_TYPE_VENDOR | REQUESTTYPE_RECIPIENT_DEVICE)) {
        return FALSE;
    }
    switch (fpga_configure_running) {
        case FALSE:
            switch (direction_in) {
                case FALSE:
                    if (data_length) {
                        return FALSE;
                    }
                    switch (command) {
                        case COMMAND_FPGA:
                            switch (subcommand) {
                                case COMMAND_FPGA_CONFIGURE_START:
                                    FPGAConfigureStart();
                                    fpga_configure_running = TRUE;
                                    break;
                                default:
                                    return FALSE;
                            }
                            break;
                        /* XXX: Would it be more appropriate to make these per-endpoint
                           instead ? */
                        case COMMAND_STOP:
                            CommandStop();
                            break;
                        case COMMAND_PAUSE:
                            CommandPause(subcommand);
                            break;
                        default:
                            return FALSE;
                    }
                    break;
                case TRUE:
                    switch (command) {
                        case COMMAND_STATUS:
                            if (data_length != 1) {
                                return FALSE;
                            }
                            EP0BUF[0] = CommandStatus();
                            EP0BCH = 0x00; SYNCDELAY;
                            EP0BCL = 0x01; SYNCDELAY;
                            break;
                        default:
                            return FALSE;
                    }
                    break;
            }
            break;
        case TRUE:
            switch (direction_in) {
                case FALSE:
                    switch (command) {
                        case COMMAND_FPGA:
                            switch (subcommand) {
                                case COMMAND_FPGA_CONFIGURE_START:
                                    if (data_length) {
                                        return FALSE;
                                    }
                                    FPGAConfigureStart();
                                    fpga_configure_running = TRUE;
                                    break;
                                case COMMAND_FPGA_CONFIGURE_WRITE:
                                    if (!data_length) {
                                        return FALSE;
                                    }
                                    fpga_configure_to_receive = data_length;
                                    EP0BCL = 0; /* arm endpoint */
                                    break;
                                case COMMAND_FPGA_CONFIGURE_STOP:
                                    if (data_length) {
                                        return FALSE;
                                    }
                                    fpga_configure_running = FALSE;
                                    FPGAConfigureStop();
                                    break;
                                default:
                                    return FALSE;
                            }
                            break;
                        default:
                            return FALSE;
                    }
                    break;
                case TRUE:
                    return FALSE;
                    break;
            }
            break;
    }
    return TRUE;
}

void handle_ep0_out(void) {
    BYTE received;
    if (fpga_configure_to_receive) {
        received = EP0BCL;
        if (received > fpga_configure_to_receive ||
                FPGAConfigureWrite(EP0BUF, received)) {
            fpga_configure_to_receive = 0;
            EP0CS |= bmHSNAK | bmEPSTALL;
        } else {
            fpga_configure_to_receive -= received;
            if (fpga_configure_to_receive) {
                EP0BCL = 0; /* re-arm endpoint */
            } else {
                EP0CS |= bmHSNAK; /* all received, handshake */
            }
        }
    }
}

void ibn_isr() __interrupt IBN_ISR {
    /* Prevent further IBN interrupts from happening until we are done
       processing this one, without preventing other (USB) interrupts from
       being serviced. */
    BYTE old_ibnie = IBNIE;
    IBNIE = 0;
    CLEAR_USBINT();
    if (IBNIRQ & bmEP2IBN) {
        if (!(EP24FIFOFLGS & bmBIT1)) {
            INPKTEND = 2;
        }
        IBNIRQ = bmEP2IBN;
    }
    NAKIRQ = bmIBN;
    IBNIE = old_ibnie;
}
