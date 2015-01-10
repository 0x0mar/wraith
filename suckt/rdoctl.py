#!/usr/bin/env python

""" rdoctl.py: Radio Controller

A "consolidated" radio with tuning , sniffing and reporting capabilities
"""
__name__ = 'rdoctl'
__license__ = 'GPL'
__version__ = '0.0.4'
__date__ = 'December 2014'
__author__ = 'Dale Patterson'
__maintainer__ = 'Dale Patterson'
__email__ = 'wraith.wireless@hushmail.com'
__status__ = 'Development'

import time                            # timestamps
import signal                          # handling signals
import socket                          # reading frames
import threading                       # for the tuner thread
from Queue import Queue, Empty         # thread-safe queue
import multiprocessing as mp           # for Process
from internal import Report            # internal communications
from wraith.radio import iw            # iw command line interface
import wraith.radio.iwtools as iwt     # nic command line interaces
from wraith.radio.mpdu import MAX_MPDU # maximum size of frame

class Tuner(threading.Thread):
    """ 'tunes' the radio's current channel and width """
    def __init__(self,ev,tC,iface,scan,dwell,i):
        """
         ev - event queue between radio and this Thread
         tC - suckt token connnection
         iface - interface name of card
         scan - scan list of channel tuples (ch,chw)
         dwell - dwell list each stay on scan[i] for dwell[i]
         i - initial channel index
        """
        threading.Thread.__init__(self)
        self._done = threading.Event()
        self._eQ = ev      # event queue from radio controller
        self._tC = tC      # connection from suckt to tuner
        self._vnic = iface # this radio's vnic
        self._chs = scan   # scan list
        self._ds = dwell   # corresponding dwell times
        self._i = i        # initial/current index into scan/dwell

    def shutdown(self): self._done.set()

    def run(self):
        """ switch channels based on associted dwell times """
        while not self._done.is_set():
            # wait on connection for channel's dwell time. If no token, switch
            # to next channel. If token & it is a hold, block on the connection
            # until we get another token (assumes suckt will not send consecutive
            # holds) which should be a resme or stop
            if self._tC.poll(self._ds[self._i]):
                token = self._tC.recv()
                if token == '!STOP!':
                    self._eQ.put(('!STOP!',time.time(),' '))
                elif token == '!HOLD!':
                    self._eQ.put(('!HOLD!',time.time(),' '))
                    token = self._tC.recv()
                    if token == '!RESUME!':
                        self._eQ.put(('!RESUME!',time.time(),' '))
                    elif token == '!STOP!':
                        self._eQ.put(('!STOP!',time.time(),' '))
            else:
                # no token, go to next channel
                try:
                    self._i = (self._i+1) % len(self._chs)
                    iw.chset(self._vnic,
                             str(self._chs[self._i][0]),
                             self._chs[self._i][1])
                except iw.IWException as e:
                    # iw related exception, set event token and stop execution
                    self._eQ.put(('!FAIL!',time.time(),e))
                except Exception as e:
                    # catch all
                    self._eQ.put(('!FAIL!',time.time(),e))

class RadioController(mp.Process):
    """ Radio - primarily placeholder for radio details """
    def __init__(self,comms,conn,conf):
        """ initialize radio
         comms - internal communications
         conn - connection to/from suckt
         conf - radio configuration dict. Must have key->value pairs for keys role,
          nic, dwell, scan and pass and optionally for keys ant_gain, ant_type,
          ant_loss, desc
         NOTE: the list of dwell times is an artifact of previous revisions. it
          is maintained here for future revisions that may implement adaptive
          scans
        """
        mp.Process.__init__(self)
        self._comms = comms   # internal deque
        self._conn = conn     # message queue to suckt
        self._q = None        # queue between tuner and us
        
        # _setup() sets the following
        self._role = None     # role this radio plays one of {RECON|COLLECTION}
        self._nic = None      # radio network interface controller name
        self._mac = None      # real mac address
        self._phy = None      # the phy of the device
        self._vnic = None     # virtual monitor name
        self._s = None        # the raw socket
        self._std = None      # supported standards
        self._chs = []        # supported channels
        self._txpwr = 0       # current tx power
        self._driver = None   # the driver
        self._chipset = None  # the chipset
        self._spoofed = ""    # spoofed mac address
        self._desc = None     # optional description
        self._scan = []       # the scan pattern: list of tuples (ch,chw)
        self._ds = []         # dwell times (stay on scan[i] for time ds[i]
        self._tuner = None    # tuner thread
        self._antenna = {}    # antenna details {type,gain,loss,offset}
        self._hop = None      # averaged hop time
        self._interval = None # interval time for a complete scan
        self._ev = None       # event queue for thread

        # set up
        self._setup(conf)

    def _setup(self,conf):
        """
         1) sets radio properties as specified in conf
         2) prepares specified nic for monitoring and binds it
         3) creates a scan list and compile statistics
        """
        # if the nic specified in conf is present, set it
        if conf['nic'] not in iwt.wifaces():
            raise RuntimeError("%s:iwtools.wifaces:not found" % conf['role'])
        self._nic = conf['nic']
        self._role = conf['role']

        # get the phy and associated interfaces
        try:
            (self._phy,ifaces) = iw.dev(self._nic)
            self._mac = ifaces[0]['addr']
        except (KeyError, IndexError):
            raise RuntimeError("%s:iw.dev:error getting interfaces" % self._role)
        except iw.IWException:
            raise RuntimeError("%s:iw.dev:failed to get phy" % self._role)

        # get properties (the below will return None rather than throw exception)
        self._chs = iw.chget(self._phy)
        self._std = iwt.iwconfig(self._nic,'Standards')
        self._txpwr = iwt.iwconfig(self._nic,'Tx-Power')
        self._driver = iwt.getdriver(self._nic)
        self._chipset = iwt.getchipset(self._driver)

        # delete all associated interfaces - we want full control
        for iface in ifaces: iw.devdel(iface['nic'])

        # determine virtual interface name
        ns = []
        for wiface in iwt.wifaces():
            cs = wiface.split('suckt')
            try:
                if len(cs) > 1: ns.append(int(cs[1]))
            except ValueError:
                pass
        n = 0 if not 0 in ns else max(ns)+1
        self._vnic = "suckt%d" % n

        # sniffing interface
        try:
            # create a monitor and turn it on
            iw.phyadd(self._phy,self._vnic,'monitor')
            iwt.ifconfig(self._vnic,'up')
        except iw.IWException as e:
            raise RuntimeError("%s:iw.phyadd:%s" % (self._role,e))
        except iwt.IWToolsException as e:
            raise RuntimeError("%s:iwtools.ifconfig:%s" % (self._role,e))

        # wrap remaining in a try block, we must attempt to restore card and
        # release the socket after any failures ATT
        try:
            # bind the socket
            self._s = socket.socket(socket.AF_PACKET,
                                    socket.SOCK_RAW,
                                    socket.htons(0x0003))
            self._s.bind((self._vnic,0x0003))
            uptime = time.time()

            # read in antenna details
            self._antenna['gain'] = conf['ant_gain']
            self._antenna['loss'] = conf['ant_loss']
            self._antenna['offset'] = conf['ant_offset']
            self._antenna['type'] = conf['ant_type']
            self._desc = conf['desc']

            # compile initial scan pattern from config
            scan = [t for t in conf['scan'] if str(t[0]) in self._chs and not t in conf['pass']]

            # sum hop times with side effect of removing any invalid channel tuples
            # i.e. Ch 14, Width HT40+ and channels the card cannot tune to
            i = 0
            self._hop = 0
            while scan:
                try:
                    t = time.time()
                    iw.chset(self._vnic,str(scan[i][0]),scan[i][1])
                    self._hop += (time.time() - t)
                    i += 1
                except iw.IWException as e:
                    if iw.ecode(str(e)) == iw.IW_INVALIDARG:
                        # error code is invalid argument, drop the channel
                        del scan[i]
                    else:
                        raise
                except IndexError:
                    # all channels checked
                    break

            if not scan:
                raise ValueError("Empty scan pattern")
            else:
                self._scan = scan

            # calculate avg hop time, and interval time
            self._hop /= len(self._scan)
            self._interval = len(self._scan) * conf['dwell'] +\
                             len(self._scan) * self._hop

            # create list of dwell times
            self._ds = [conf['dwell']] * len(self._scan)

            # get start ch & set the initial channel
            try:
                ch_i = self._scan.index(conf['scan_start'])
            except ValueError:
                ch_i = 0
            iw.chset(self._vnic,str(self._scan[ch_i][0]),self._scan[ch_i][1])

            # initialize tuner thread
            self._q = Queue()
            self._tuner = Tuner(self._q,self._conn,self._vnic,self._scan,self._ds,ch_i)

            # notify RTO we are good
            self._comms.put(Report(self._vnic,uptime,'!UP!',self.radio))
        except socket.error as e:
            try:
                iw.devdel(self._vnic)
                iw.phyadd(self._phy,self._nic)
            except iw.IWException:
                pass
            raise RuntimeError("%s:Socket:%s" % (self._role,e))
        except iw.IWException as e:
            try:
                iw.devdel(self._vnic)
                iw.phyadd(self._phy,self._nic)
            except iw.IWException:
                pass
            self._s.close()
            self._s = None
            raise RuntimeError("%s:iw.chset:Failed to set channel: %s" % (self._role,e))
        except (ValueError,TypeError) as e:
            try:
                iw.devdel(self._vnic)
                iw.phyadd(self._phy,self._nic)
            except iw.IWException:
                pass
            self._s.close()
            self._s = None
            raise RuntimeError("%s:config:%s" % (self._role,e))
        except Exception as e:
            # blanket exception
            try:
                iw.devdel(self._vnic)
                iw.phyadd(self._phy,self._nic)
            except iw.IWException:
                pass
            self._s.close()
            self._s = None
            raise RuntimeError("%s:Unknown:%s" % (self._role,e))

    def terminate(self): pass

    def run(self):
        """ run execution loop """
        # ignore signals being used by main program
        signal.signal(signal.SIGINT,signal.SIG_IGN)
        signal.signal(signal.SIGTERM,signal.SIG_IGN)

        # start tuner thread TODO: what to do on failure of tuner thread??
        self._tuner.start()
        self._comms.put(Report(self._vnic,time.time(),'!SCAN!',self._scan))

        # execute sniffing loop
        while True:
            try:
                # check for any notifications from tuner thread
                (event,ts,msg) = self._q.get_nowait()
            except Empty:
                # no notices from tuner thread, pull of the next frame
                try:
                    # pull the frame off and pass it on
                    frame = self._s.recv(MAX_MPDU)
                    self._comms.put(Report(self._vnic,time.time(),'!FRAME!',frame))
                except socket.error as e:
                    self._comms.put(Report(self._vnic,time.time(),'!FAIL!',e))
                    self._conn.send(('err',"%s" % self._role,'Socket',e))
                    break
                except Exception as e:
                    # blanket 'don't know what happend' exception
                    self._comms.put(Report(self._vnic,time.time(),'!FAIL!',e))
                    self._conn.send(('err',"%s" % self._role,'Unknown',e))
                    break
            else:
                # process the notification
                if event == '!FAIL!':
                    self._comms.put(Report(self._vnic,ts,'!FAIL!',msg))
                elif event == '!HOLD!':
                    #self._comms.put(Report(ts,self._vnic,'!HOLD!',' '))
                    pass
                elif event == '!RESUME!':
                    self._comms.put(Report(self._vnic,time.time(),'!SCAN!',self._scan))
                elif event == '!LISTEN':
                    #self._comms.put(Report(self._vnic,time.time(),'!LISTEN!',' '))
                    pass
                elif event == '!PAUSE!':
                    #self._comms.put(Report(self._vnic,time.time(),'!SCAN!',' '))
                    pass
                elif event == '!STOP!':
                    break

        # shut down
        if not self.shutdown():
            try:
                self._conn.send(('warn',self._role,'Shutdown',"Incomplete reset"))
            except IOError:
                # most likely suckt already closed their side
                pass

    def shutdown(self):
        """
         attempt to restore everything and clean up. returns whether a full reset or
         not occurred
        """
        # try shutting down & resetting radio (if it failed we may not be able to)
        clean = True
        try:
            # stop the tuner
            try:
                # call shutdown and join timing out if necessary
                self._tuner.shutdown()
                self._tuner.join(max(self._ds)*2)
            except:
                clean = False
            else:
                self._tuner = None

            # reset the device
            try:
                iw.devdel(self._vnic)
                iw.phyadd(self._phy,self._nic)
            except iw.IWException:
                clean = False

            # close socket and connection
            if self._s: self._s.close()
            self._conn.close()
        except:
            clean = False
        return clean

    @property
    def radio(self):
        """ returns a dict describing this radio """
        return {'nic':self._nic,
                'vnic':self._vnic,
                'phy':self._phy,
                'mac':self._mac,
                'role':self._role,
                'spoofed':self._spoofed,
                'driver':self._driver,
                'chipset':self._chipset,
                'standards':self._std,
                'channels':self._chs,
                'txpwr':self._txpwr,
                'desc':self._desc,
                'ant_type':self._antenna['type'],
                'ant_gain':self._antenna['gain'],
                'ant_loss':self._antenna['loss'],
                'ant_offset':self._antenna['offset']}