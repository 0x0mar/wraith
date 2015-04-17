#!/usr/bin/env python
""" wraith-rt.py - defines the Master(main) Panel of the wraith gui
"""

#__name__ = 'wraith-rt'
__license__ = 'GPL v3.0'
__version__ = '0.0.3'
__revdate__ = 'February 2015'
__author__ = 'Dale Patterson'
__maintainer__ = 'Dale Patterson'
__email__ = 'wraith.wireless@yandex.com'
__status__ = 'Development'

import os                                  # file info etc
import sys                                 # for exit
import time                                # sleeping, timestamps
import psycopg2 as psql                    # postgresql api
import Tkinter as tk                       # gui constructs
import ttk                                 # ttk widgets
#from PIL import Image,ImageTk              # image input & support
import ConfigParser                        # config file parsing
import argparse                            # cmd line arguments
import wraith                              # version info
import wraith.widgets.panel as gui         # graphics suite
import wraith.subpanels as subgui          # our subpanels
from wraith.utils import bits              # bitmask functions
from wraith.utils import cmdline           # command line stuff

# server/services functions shared by splash and main panel

def startpsql(pwd):
    """
     start postgresl server
      pwd: the sudo password
    """
    try:
        cmdline.service('postgresql',pwd)
        time.sleep(0.5)
        if not cmdline.runningprocess('postgres'): raise RuntimeError
    except RuntimeError:
        return False
    else:
        return True

def stoppsql(pwd):
    """
     shut down posgresql.
     pwd: the sudo password
    """
    try:
        cmdline.service('postgresql',pwd,False)
        while cmdline.runningprocess('postgres'): time.sleep(0.5)
    except RuntimeError:
        return False
    else:
        return True

def startnidus(pwd):
    """
     start nidus server
     pwd: the sudo password
    """
    try:
        cmdline.service('nidusd',pwd)
        time.sleep(0.5)
        if not cmdline.nidusrunning(wraith.NIDUSPID): raise RuntimeError
    except RuntimeError:
        return False
    else:
        return True

def stopnidus(pwd):
    """
     stop nidus storage manager.
     pwd: Sudo password
    """
    try:
        cmdline.service('nidusd',pwd,False)
        while cmdline.nidusrunning(wraith.NIDUSPID): time.sleep(1.0)
    except RuntimeError as e:
        return False
    else:
        return True

def startdyskt(pwd):
    """
     start dyskt sensor
     pwd: the sudo password
    """
    try:
        cmdline.service('dysktd',pwd)
        time.sleep(1.0)
        if not cmdline.dysktrunning(wraith.DYSKTPID): raise RuntimeError
    except RuntimeError:
        return False
    else:
        return True

def stopdyskt(pwd):
    """
     stop dyskt sensor
     pwd: Sudo password
    """
    try:
        cmdline.service('dysktd',pwd,False)
        while cmdline.nidusrunning(wraith.DYSKTPID): time.sleep(1.0)
    except RuntimeError as e:
        return False
    else:
        return True

def psqlconnect(connect):
    """
     connect to postgresql db with connect string
     returns a tuple (conn,msg) where conn = None and msg = error string
     on failure otherwise conn = psql connection and msg = ''
    """
    conn = None
    curs = None
    try:
        conn = psql.connect(host=connect['host'],port=connect['port'],
                            dbname=connect['db'],user=connect['user'],
                            password=connect['pwd'])

        # set to use UTC
        curs = conn.cursor()
        curs.execute("set time zone 'UTC';")
    except psql.OperationalError as e:
        if e.__str__().find('connect') > 0:
            err = "PostgreSQL is not running"
        elif e.__str__().find('authentication') > 0:
            err = "Authentication string is invalid"
        else:
            err = "Unspecified DB error occurred"
            conn.rollback()
        return None,err
    else:
        conn.commit()
        return conn,''
    finally:
        if curs: curs.close()

def readconf():
    """ read in configuration file """
    cp = ConfigParser.RawConfigParser()
    if not cp.read("wraith.conf"): return "wraith.conf does not exist"

    conf = {}
    try:
        ## STORAGE
        conf['store'] = {'host':cp.get('Storage','host'),
                         'port':cp.getint('Storage','port'),
                         'db':cp.get('Storage','db'),
                         'user':cp.get('Storage','user'),
                         'pwd':cp.get('Storage','pwd')}

        ## POLICY
        conf['policy'] = {'polite':True,'shutdown':True}

        if cp.has_option('Policy','polite'):
            if cp.get('Policy','polite').lower() == 'off':
                conf['policy']['polite'] = False
        if cp.has_option('Policy','shutdown'):
            if cp.get('Policy','shutdown').lower() == 'manual':
                conf['ploicy']['shutdown'] = False

        # return no errors
        return conf
    except (ConfigParser.NoSectionError,ConfigParser.NoOptionError) as e:
        return {'err':e.__str__()}

#### STATE DEFINITIONS
_STATE_INIT_   = 0
_STATE_STORE_  = 1
_STATE_CONN_   = 2
_STATE_NIDUS_  = 3
_STATE_DYSKT_  = 4
_STATE_EXIT_   = 5
_STATE_FLAGS_NAME_ = ['init','store','conn','nidus','dyskt','exit']
_STATE_FLAGS_ = {'init':(1 << 0),   # initialized properly
                 'store':(1 << 1),  # storage instance is running (i.e. postgresql)
                 'conn':(1 << 2),   # connected to storage instance
                 'nidus':(1 << 3),  # nidus storage manager running
                 'dyskt':(1 << 4),  # at least one sensor is collecting data
                 'exit':(1 << 5)}   # exiting/shutting down

class WraithPanel(gui.MasterPanel):
    """ WraithPanel - master panel for wraith gui """
    def __init__(self,tl,pwd=None,start=False):
        """
         set up, initialize parent and then initialize the gui
         our variables
         tl: tk's topleve
         pwd: sudo password
         start: True->start all services
        """
        self._conf = None   # configuration
        self._state = 0     # bitmask state
        self._conn = None   # connection to data storage
        self._bSQL = False  # postgresql was running on startup
        self._pwd = pwd     # sudo password (should we not save it?)
        self._start = start # start everything?

        # set up super
        gui.MasterPanel.__init__(self,tl,"Wraith  v%s" % wraith.__version__,
                                 [],True,"widgets/icons/wraith3.png",False)

#### PROPS

    @property
    def getstate(self): return self._state

    @property
    def getstateflags(self): return bits.bitmask_list(_STATE_FLAGS_,self._state)

#### OVERRIDES

    def _initialize(self):
        """ initialize gui, determine initial state """
        # configure panel & write initial message
        # have to manually enter the desired size, as the menu does not expand
        # the visibile portion automatically
        self.tk.wm_geometry("300x1+0+0")
        self.logwrite("Wraith v%s" % wraith.__version__)

        # read in conf file, exit on error
        self._conf = readconf()
        if 'err' in self._conf:
            self.logwrite("Configuration file is invalid. %s" % self._conf['err'],gui.LOG_ERR)
            return

        # determine if postgresql is running
        if cmdline.runningprocess('postgres'):
            # update self,msg and connect
            self.logwrite('PostgreSQL is running',gui.LOG_NOTE)
            self._bSQL = True
            self._setstate(_STATE_STORE_)
            (self._conn,msg) = psqlconnect(self._conf['store'])
            if not self._conn is None: self._setstate(_STATE_CONN_)
            else:
                self.logwrite("Error connecting to PostgreSQL: %s" % msg,gui.LOG_ERR)
                return
        else:
            self.logwrite("PostgreSQL is not running",gui.LOG_WARN)
            self.logwrite("Not connected to database",gui.LOG_WARN)

        # nidus running?
        if cmdline.nidusrunning(wraith.NIDUSPID):
            self.logwrite("Nidus is running")
            self._setstate(_STATE_NIDUS_)
        else:
            self.logwrite("Nidus is not running",gui.LOG_WARN)

        if cmdline.dysktrunning(wraith.DYSKTPID):
            self.logwrite("DySKT is running")
            self._setstate(_STATE_DYSKT_)
        else:
            self.logwrite("DySKT is not running",gui.LOG_WARN)

        # set initial state to initialized
        self._setstate(_STATE_INIT_)

        # adjust menu options accordingly
        self._menuenable()

    def _shutdown(self):
        """ if connected to datastorage, closes connection """
        # set the state
        self._setstate(_STATE_EXIT_)

        # shutdown dyskt
        self._stopsensor()

        # shutdown storage
        self._stopstorage()

    def _makemenu(self):
        """ make the menu """
        self.menubar = tk.Menu(self)

        # Wraith Menu
        # all options will always be enabled
        self.mnuWraith = tk.Menu(self.menubar,tearoff=0)
        self.mnuWraithGui = tk.Menu(self.mnuWraith,tearoff=0)
        self.mnuWraithGui.add_command(label='Save',command=self.guisave)
        self.mnuWraithGui.add_command(label='Load',command=self.guiload)
        self.mnuWraith.add_cascade(label='Gui',menu=self.mnuWraithGui)
        self.mnuWraith.add_separator()
        self.mnuWraith.add_command(label='Configure',command=self.configwraith)
        self.mnuWraith.add_separator()
        self.mnuWraith.add_command(label='Exit',command=self.delete)

        # Tools Menu
        # all options will always be enabled
        self.mnuTools = tk.Menu(self.menubar,tearoff=0)
        self.mnuTools.add_command(label='Convert',command=self.viewconvert)
        self.mnuToolsCalcs = tk.Menu(self.mnuTools,tearoff=0)
        self.mnuToolsCalcs.add_command(label='EIRP',command=lambda:self.calc('EIRP'))
        self.mnuToolsCalcs.add_command(label='FSPL',command=lambda:self.calc('FSPL'))
        self.mnuToolsCalcs.add_command(label='Link Budget',command=lambda:self.calc('Link Budget'))
        self.mnuToolsCalcs.add_command(label="Fresnel Zone",command=lambda:self.calc('Fresnel Zone'))
        self.mnuToolsCalcs.add_separator()
        self.mnuToolsCalcs.add_command(label='Distance',command=lambda:self.calc('Distance'))
        self.mnuToolsCalcs.add_command(label='Terminus',command=lambda:self.calc('Terminus'))
        self.mnuToolsCalcs.add_command(label='Cut',command=lambda:self.calc('Cut'))
        self.mnuTools.add_cascade(label='Calcuators',menu=self.mnuToolsCalcs)

        # View Menu
        # all options will always be enabled
        self.mnuView = tk.Menu(self.menubar,tearoff=0)
        self.mnuView.add_command(label='Data Bins',command=self.viewdatabins)
        self.mnuView.add_separator()
        self.mnuView.add_command(label='Data',command=self.viewdata)

        # Storage Menu
        self.mnuStorage = tk.Menu(self.menubar,tearoff=0)
        self.mnuStorage.add_command(label="Start All",command=self.storagestart)  # 0
        self.mnuStorage.add_command(label="Stop All",command=self.storagestop)    # 1
        self.mnuStorage.add_separator()                                           # 2
        self.mnuStoragePSQL = tk.Menu(self.mnuStorage,tearoff=0)
        self.mnuStoragePSQL.add_command(label='Start',command=self.psqlstart)       # 0
        self.mnuStoragePSQL.add_command(label='Stop',command=self.psqlstop)         # 1
        self.mnuStoragePSQL.add_separator()                                         # 2
        self.mnuStoragePSQL.add_command(label='Connect',command=self.connect)       # 3
        self.mnuStoragePSQL.add_command(label='Disconnect',command=self.disconnect) # 4      # 1
        self.mnuStoragePSQL.add_separator()                                         # 5
        self.mnuStoragePSQL.add_command(label='Fix',command=self.psqlfix)           # 6
        self.mnuStoragePSQL.add_command(label='Delete All',command=self.psqldelall) # 7
        self.mnuStorage.add_cascade(label='PostgreSQL',menu=self.mnuStoragePSQL)  # 3
        self.mnuStorageNidus = tk.Menu(self.mnuStorage,tearoff=0)
        self.mnuStorageNidus.add_command(label='Start',command=self.nidusstart)     # 0
        self.mnuStorageNidus.add_command(label='Stop',command=self.nidusstop)       # 1
        self.mnuStorageNidus.add_separator()                                        # 2
        self.mnuNidusLog = tk.Menu(self.mnuStorageNidus,tearoff=0)
        self.mnuNidusLog.add_command(label='View',command=self.viewniduslog)         # 0
        self.mnuNidusLog.add_command(label='Clear',command=self.clearniduslog)       # 1
        self.mnuStorageNidus.add_cascade(label='Log',menu=self.mnuNidusLog)         # 3
        self.mnuStorageNidus.add_separator()                                        # 4
        self.mnuStorageNidus.add_command(label='Config',command=self.confignidus)   # 5
        self.mnuStorage.add_cascade(label='Nidus',menu=self.mnuStorageNidus)      # 4

        # DySKT Menu
        self.mnuDySKT = tk.Menu(self.menubar,tearoff=0)
        self.mnuDySKT.add_command(label='Start',command=self.dysktstart)   # 0
        self.mnuDySKT.add_command(label='Stop',command=self.dysktstop)     # 1
        self.mnuDySKT.add_separator()                                      # 2
        self.mnuDySKT.add_command(label='Control',command=self.dysktctrl)  # 3
        self.mnuDySKT.add_separator()                                      # 4
        self.mnuDySKTLog = tk.Menu(self.mnuDySKT,tearoff=0)
        self.mnuDySKTLog.add_command(label='View',command=self.viewdysktlog)   # 0
        self.mnuDySKTLog.add_command(label='Clear',command=self.cleardysktlog) # 1
        self.mnuDySKT.add_cascade(label='Log',menu=self.mnuDySKTLog)       # 5
        self.mnuDySKT.add_separator()                                      # 6
        self.mnuDySKT.add_command(label='Config',command=self.configdyskt) # 7

        # Help Menu
        self.mnuHelp = tk.Menu(self.menubar,tearoff=0)
        self.mnuHelp.add_command(label='About',command=self.about)
        self.mnuHelp.add_command(label='Help',command=self.help)

        # add the menus
        self.menubar.add_cascade(label='Wraith',menu=self.mnuWraith)
        self.menubar.add_cascade(label="Tools",menu=self.mnuTools)
        self.menubar.add_cascade(label='View',menu=self.mnuView)
        self.menubar.add_cascade(label='Storage',menu=self.mnuStorage)
        self.menubar.add_cascade(label='DySKT',menu=self.mnuDySKT)
        self.menubar.add_cascade(label='Help',menu=self.mnuHelp)

#### MENU CALLBACKS

#### Wraith Menu

    def configwraith(self):
        """ display config file preference editor """
        panel = self.getpanels('preferences',False)
        if not panel:
            t = tk.Toplevel()
            pnl = subgui.WraithConfigPanel(t,self)
            self.addpanel(pnl.name,gui.PanelRecord(t,pnl,'preferences'))
        else:
            panel[0].tk.deiconify()
            panel[0].tk.lift()

#### Tools Menu

    def viewconvert(self):
        """ display conversion panel """
        panel = self.getpanels('convert',False)
        if not panel:
            t = tk.Toplevel()
            pnl = subgui.ConvertPanel(t,self)
            self.addpanel(pnl.name,gui.PanelRecord(t,pnl,'convert'))
        else:
            panel[0].tk.deiconify()
            panel[0].tk.lift()

    def calc(self,key):
        """ calculate the function defined by key in the _CALCS_ dict"""
        panel = self.getpanels('%scalc' % key)
        if not panel:
            t = tk.Toplevel()
            pnl = subgui.CalculatePanel(t,self,key,subgui.CALCS[key]['inputs'],
                                                   subgui.CALCS[key]['answer'],
                                                   subgui.CALCS[key]['rc'])
            self.addpanel(pnl.name,gui.PanelRecord(t,pnl,'%scalc' % key))
        else:
            panel[0].tk.deiconify()
            panel[0].tk.lift()

#### View Menu

    def viewdatabins(self):
        """ display the data bins panel """
        panel = self.getpanels('databin',False)
        if not panel:
            t = tk.Toplevel()
            pnl = subgui.DataBinPanel(t,self)
            self.addpanel(pnl.name,gui.PanelRecord(t,pnl,'databin'))
        else:
            panel[0].tk.deiconify()
            panel[0].tk.lift()

    def viewdata(self):
        """ display data panel """
        self.unimplemented()

#### Storage Menu

    def storagestart(self):
        """ starts database and storage manager """
        self._startstorage()
        self._updatestate()
        self._menuenable()

    def storagestop(self):
        """ stops database and storage manager """
        self._stopstorage()
        self._updatestate()
        self._menuenable()

    def connect(self):
        """ connects to postgresql """
        # NOTE: this should not be enabled unless psql is running and we are
        # not connected, but check anyway
        flags = bits.bitmask_list(_STATE_FLAGS_,self._state)
        if not flags['conn']:
            psqlconnect(self._conf['store'])
            self._updatestate()
            self._menuenable()

    def disconnect(self):
        """ connects to postgresql """
        # NOTE: should not be enabled if already disconnected but check anyway
        # TODO: what to do once we have data being pulled?
        flags = bits.bitmask_list(_STATE_FLAGS_,self._state)
        if flags['conn']:
            self._psqldisconnect()
            self._updatestate()
            self._menuenable()

    def psqlstart(self):
        """ starts postgresql """
        # NOTE: should not be enabled if postgresql is already running but check anyway
        flags = bits.bitmask_list(_STATE_FLAGS_,self._state)
        if not flags['store']:
            # do we have a password
            if not self._pwd:
                pwd = self._getpwd()
                if pwd is None:
                    self.logwrite("Password entry canceled. Cannot continue",
                                  gui.LOG_WARN)
                    return
                self._pwd = pwd
            self.logwrite("Starting Postgresql...",gui.LOG_NOTE)
            if startpsql(self._pwd):
                self.logwrite('Postgresql started')
                self._updatestate()
                self._menuenable()
            else:
                self.logwrite("Postgresql failed to start")

    def psqlstop(self):
        """ starts postgresql """
        # should not be enabled if postgresql is not running, but check anyway
        flags = bits.bitmask_list(_STATE_FLAGS_,self._state)
        if flags['store'] and not flags['dyskt']:
            # do we have a password
            if not self._pwd:
                pwd = self._getpwd()
                if pwd is None:
                    self.logwrite("Password entry canceled. Cannot continue",
                                  gui.LOG_WARN)
                    return
                self._pwd = pwd
            if stoppsql(self._pwd):
                self._updatestate()
                self._menuenable()
            else:
                self.logwrite("Failed to stop PostgreSQL",gui.LOG_ERR)

    def psqlfix(self):
        """
         fix any open-ended periods left over by errors. An error or kill -9
         may leave periods in certain tables as NULL-ended which will throw an
         error during insert of new records
        """
        # should not be enabled unless postgres is running, we are connected
        # and a sensor is not running, but check anyway
        flags = bits.bitmask_list(_STATE_FLAGS_,self._state)
        if flags['store'] and flags['conn'] and not flags['dyskt']:
            curs = None
            try:
                self.logwrite("Fixing null-ended records in database...",gui.LOG_NOTE)
                curs = self._conn.cursor()
                curs.callproc("fix_nullperiod")
                self._conn.commit()
                self.logwrite("Fixed all null-ended records")
            except psql.Error as e:
                self._conn.rollback()
                self.logwrite("Error fixing records <%s: %s>" % (e.pgcode,e.pgerror),
                              gui.LOG_ERR)
            finally:
                if curs: curs.close()

    def psqldelall(self):
        """ delete all data in nidus database """
        # should not be enabled unless postgres is running, we are connected
        # and a sensor is not running, but check anyway
        flags = bits.bitmask_list(_STATE_FLAGS_,self._state)
        if flags['store'] and flags['conn'] and not flags['dyskt']:
            ans = self.ask('Delete Records','Delete all DB records?')
            if ans == 'no': return
            curs = None
            try:
                # get a cursor and execute the delete_all procedure
                self.logwrite("Deleting all records in database...",gui.LOG_NOTE)
                curs = self._conn.cursor()
                curs.callproc('delete_all')
                self._conn.commit()
                self.logwrite("Deleted all records")
            except psql.Error as e:
                self._conn.rollback()
                self.logwrite("Delete Failed<%s: %s>" % (e.pgcode,e.pgerror),gui.LOG_ERR)
            finally:
                if curs: curs.close()

    def nidusstart(self):
        """ starts nidus storage manager """
        # NOTE: should not be enabled if postgresql is not running but check anyway
        flags = bits.bitmask_list(_STATE_FLAGS_,self._state)
        if not flags['nidus']:
            # do we have a password
            if not self._pwd:
                pwd = self._getpwd()
                if pwd is None:
                    self.logwrite("Password entry canceled. Cannot continue",
                                  gui.LOG_WARN)
                    return
                self._pwd = pwd
            if startnidus(self._pwd):
                self._updatestate()
                self._menuenable()
            else:
                self.logwrite("Failed to start Nidus",gui.LOG_ERR)

    def nidusstop(self):
        """ stops nidus storage manager """
        # should not be enabled if nidus is not running, but check anyway
        flags = bits.bitmask_list(_STATE_FLAGS_,self._state)
        if flags['nidus']:
            # do we have a password
            if not self._pwd:
                pwd = self._getpwd()
                if pwd is None:
                    self.logwrite("Password entry canceled. Cannot continue",
                                  gui.LOG_WARN)
                    return
                self._pwd = pwd

            self.logwrite("Starting Nidus...",gui.LOG_NOTE)
            if stopnidus(self._pwd):
                self._updatestate()
                self._menuenable()
            else:
                self.logwrite("Failed to stop Nidus",gui.LOG_ERR)

    def viewniduslog(self):
        """ display Nidus log """
        panel = self.getpanels('niduslog',False)
        if not panel:
            t = tk.Toplevel()
            pnl = gui.TailLogPanel(t,self,"Nidus Log",200,wraith.NIDUSLOG,40)
            self.addpanel(pnl.name,gui.PanelRecord(t,pnl,'niduslog'))
        else:
            panel[0].tk.deiconify()
            panel[0].tk.lift()

    def clearniduslog(self):
        """ clear nidus log """
        # prompt first
        finfo = os.stat(wraith.NIDUSLOG)
        if finfo.st_size > 0:
            ans = self.ask("Clear Log","Clear contents of Nidus log?")
            if ans == 'no': return
            lv = self.getpanel('niduslog')
            with open(wraith.NIDUSLOG,'w'): os.utime(wraith.NIDUSLOG,None)
            if lv: lv.reset()

    def confignidus(self):
        """ display nidus config file preference editor """
        panel = self.getpanels('nidusprefs',False)
        if not panel:
            t = tk.Toplevel()
            pnl = subgui.NidusConfigPanel(t,self)
            self.addpanel(pnl.name,gui.PanelRecord(t,pnl,'nidusprefs'))
        else:
            panel[0].tk.deiconify()
            panel[0].tk.lift()

#### DySKT Menu

    def dysktstart(self):
        """ starts DySKT sensor """
        self._startsensor()
        self._updatestate()
        self._menuenable()

    def dysktstop(self):
        """ stops DySKT sensor """
        self._stopsensor()
        self._updatestate()
        self._menuenable()

    def dysktctrl(self):
        """ displays DySKT Control Panel """
        self.unimplemented()

    def viewdysktlog(self):
        """ display DySKT log """
        panel = self.getpanels('dysktlog',False)
        if not panel:
            t = tk.Toplevel()
            pnl = gui.TailLogPanel(t,self,"DySKT Log",200,wraith.DYSKTLOG,40)
            self.addpanel(pnl.name,gui.PanelRecord(t,pnl,'dysktlog'))
        else:
            panel[0].tk.deiconify()
            panel[0].tk.lift()

    def cleardysktlog(self):
        """ clears the DySKT log """
        # prompt first
        finfo = os.stat(wraith.DYSKTLOG)
        if finfo.st_size > 0:
            ans = self.ask("Clear Log","Clear contents of DySKT log?")
            if ans == 'no': return
            with open(wraith.DYSKTLOG,'w'): pass
            lv = self.getpanel('dysktlog')
            if lv: lv.reset()

    def configdyskt(self):
        """ display dyskt config file preference editor """
        panel = self.getpanels('dysktprefs',False)
        if not panel:
            t = tk.Toplevel()
            pnl = subgui.DySKTConfigPanel(t,self)
            self.addpanel(pnl.name,gui.PanelRecord(t,pnl,'dysktprefs'))
        else:
            panel[0].tk.deiconify()
            panel[0].tk.lift()

#### HELP MENU

    def about(self):
        """ display the about panel """
        panel = self.getpanels('about',False)
        if not panel:
            t = tk.Toplevel()
            pnl = subgui.AboutPanel(t,self)
            self.addpanel(pnl.name,gui.PanelRecord(t,pnl,'about'))
        else:
            panel[0].tk.deiconify()
            panel[0].tk.lift()

    def help(self):
        """ display the help panel """
        self.unimplemented()

#### MINION METHODS

    def showpanel(self,desc):
        """ opens a panel of type desc """
        if desc == 'log': self.viewlog()
        elif desc == 'databin': self.viewdatabins()
        elif desc == 'preferences': self.configwraith()
        elif desc == 'niduslog': self.viewniduslog()
        elif desc == 'nidusprefs': self.confignidus()
        elif desc == 'dysktlog': self.viewdysktlog()
        elif desc == 'dysktprefs': self.configdyskt()
        elif desc == 'about': self.about()
        else: raise RuntimeError, "WTF Cannot open %s" % desc

#### HELPER FUNCTIONS

    def _updatestate(self):
        """ reevaluates internal state """
        # state of nidus
        if cmdline.nidusrunning(wraith.NIDUSPID): self._setstate(_STATE_NIDUS_)
        else: self._setstate(_STATE_NIDUS_,False)

        # state of dyskt
        if cmdline.dysktrunning(wraith.DYSKTPID): self._setstate(_STATE_DYSKT_)
        else:  self._setstate(_STATE_DYSKT_,False)

        # state of postgres i.e. store
        if cmdline.runningprocess('postgres'): self._setstate(_STATE_STORE_)
        else: self._setstate(_STATE_STORE_,False)

        # state of our connection - should figure out a way to determine if
        # connection is still 'alive'
        if self._conn: self._setstate(_STATE_CONN_)
        else: self._setstate(_STATE_CONN_,False)

    def _setstate(self,f,up=True):
        """ sets internal state's flag f to 1 if up is True or 0 otherwise """
        if up:
            self._state = bits.bitmask_set(_STATE_FLAGS_,
                                           self._state,
                                           _STATE_FLAGS_NAME_[f])
        else:
            self._state = bits.bitmask_unset(_STATE_FLAGS_,
                                             self._state,
                                             _STATE_FLAGS_NAME_[f])

    def _menuenable(self):
        """ enable/disable menus as necessary """
        # get all flags
        flags = bits.bitmask_list(_STATE_FLAGS_,self._state)

        # adjust storage menu
        # easiest for storage is to disable all and then only enable relevant
        # always allow Nidus->config, Nidus->View Log
        self.mnuStorage.entryconfig(0,state=tk.DISABLED)      # all start
        self.mnuStorage.entryconfig(1,state=tk.DISABLED)      # all stop
        self.mnuStoragePSQL.entryconfig(0,state=tk.DISABLED)  # psql start
        self.mnuStoragePSQL.entryconfig(1,state=tk.DISABLED)  # psql stop
        self.mnuStoragePSQL.entryconfig(3,state=tk.DISABLED)  # connect 2 psql
        self.mnuStoragePSQL.entryconfig(4,state=tk.DISABLED)  # disconnect from psql
        self.mnuStoragePSQL.entryconfig(6,state=tk.DISABLED)  # psql fix
        self.mnuStoragePSQL.entryconfig(7,state=tk.DISABLED)  # psql delete all
        self.mnuStorageNidus.entryconfig(0,state=tk.DISABLED) # nidus start
        self.mnuStorageNidus.entryconfig(1,state=tk.DISABLED) # nidus stop
        self.mnuNidusLog.entryconfig(1,state=tk.DISABLED)     # nidus log clear

        if flags['store']:
            # storage is running enable stop all, stop postgresql (if dyskt is
            # not running)
            self.mnuStorage.entryconfig(1,state=tk.NORMAL)
            if not flags['dyskt']: self.mnuStoragePSQL.entryconfig(1,state=tk.NORMAL)
        else:
            # storage is not running, enable start all, start postgresql
            self.mnuStorage.entryconfig(0,state=tk.NORMAL)
            self.mnuStoragePSQL.entryconfig(0,state=tk.NORMAL)

        if flags['nidus']:
            # nidus is running, enable stop all, stop nidus
            self.mnuStorage.entryconfig(1,state=tk.NORMAL)
            self.mnuStorageNidus.entryconfig(1,state=tk.NORMAL)
        else:
            # nidus is not running, enable start all & clear nidus log
            # enable start nidus only if postgres is running
            self.mnuStorage.entryconfig(0,state=tk.NORMAL)
            self.mnuStorageNidus.entryconfig(0,state=tk.NORMAL)
            self.mnuNidusLog.entryconfig(1,state=tk.NORMAL)

        if flags['conn']:
            # connected to psql, enable stop all and disconnect
            # if no DysKT is running, enable fix psql and delete all
            self.mnuStorage.entryconfig(1,state=tk.NORMAL)
            self.mnuStoragePSQL.entryconfig(4,state=tk.NORMAL)
            if not flags['dyskt']:
                self.mnuStoragePSQL.entryconfig(6,state=tk.NORMAL)  # psql fix
                self.mnuStoragePSQL.entryconfig(7,state=tk.NORMAL)  # psql delete all
        else:
            # disconnected, enable start all, enable connect if postgres is running
            self.mnuStorage.entryconfig(0,state=tk.NORMAL)
            if flags['store']: self.mnuStoragePSQL.entryconfig(3,state=tk.NORMAL)

        # adjust dyskt menu
        if not flags['store'] and not flags['nidus']:
            # cannot start/stop/control dyskt unless nidus & postgres is running
            self.mnuDySKT.entryconfig(0,state=tk.DISABLED)  # start
            self.mnuDySKT.entryconfig(1,state=tk.DISABLED)  # stop
            self.mnuDySKT.entryconfig(3,state=tk.DISABLED)  # ctrl panel
            self.mnuDySKTLog.entryconfig(1,state=tk.NORMAL) # clear log
            self.mnuDySKT.entryconfig(7,state=tk.NORMAL)    # configure
        else:
            if flags['dyskt']:
                # DySKT sensor is running
                self.mnuDySKT.entryconfig(0,state=tk.DISABLED)    # start
                self.mnuDySKT.entryconfig(1,state=tk.NORMAL)      # stop
                self.mnuDySKT.entryconfig(3,state=tk.NORMAL)      # ctrl panel
                self.mnuDySKTLog.entryconfig(1,state=tk.DISABLED) # clear log
                self.mnuDySKT.entryconfig(7,state=tk.NORMAL)      # configure
            else:
                # DySKT sensor is not running
                self.mnuDySKT.entryconfig(0,state=tk.NORMAL)    # start
                self.mnuDySKT.entryconfig(1,state=tk.DISABLED)  # stop
                self.mnuDySKT.entryconfig(3,state=tk.DISABLED)  # ctrl panel
                self.mnuDySKTLog.entryconfig(1,state=tk.NORMAL) # clear log
                self.mnuDySKT.entryconfig(7,state=tk.NORMAL)    # configure

    def _startstorage(self):
        """ start postgresql db and nidus storage manager & connect to db """
        # do we have a password
        if not self._pwd:
            pwd = self._getpwd()
            if pwd is None:
                self.logwrite("Password entry canceled. Cannot continue",gui.LOG_WARN)
                return
            self._pwd = pwd

        # start necessary storage components
        flags = bits.bitmask_list(_STATE_FLAGS_,self._state)
        if not flags['store']:
            self.logwrite("Starting PostgreSQL...",gui.LOG_NOTE)
            if not startpsql(self._pwd):
                self.logwrite("Failed to start PostgreSQL",gui.LOG_ERR)
                return
            else:
                self.logwrite("PostgreSQL started")

        # start nidus
        flags = bits.bitmask_list(_STATE_FLAGS_,self._state)
        if not flags['nidus'] and flags['store']:
            self.logwrite("Starting Nidus...",gui.LOG_NOTE)
            if not startnidus(self._pwd):
                self.logwrite("Failed to start Nidus",gui.LOG_ERR)
                return
            else:
                self.logwrite("Nidus started")

        # connect to db
        flags = bits.bitmask_list(_STATE_FLAGS_,self._state)
        if not flags['conn'] and flags['store']:
            self.logwrite("Connected to database...",gui.LOG_NOTE)
            self._conn,msg = psqlconnect(self._conf['strore'])
            if self._conn is None:
                self.logwrite(msg,gui.LOG_ERR)
                return
            else:
                self.logwrite("Connected to database")

        self._updatestate()
        self._menuenable()

    def _stopstorage(self):
        """ stop posgresql db, nidus storage manager & disconnect """
        # if DySKT is running, prompt for clearance
        flags = bits.bitmask_list(_STATE_FLAGS_,self._state)
        if flags['dyskt']:
            ans = self.ask('DySKT Running','Quit and lose queued data?')
            if ans == 'no': return

        # return if no storage component is running
        if not (flags['store'] or flags['conn'] or flags['nidus']): return

        # disconnect from db
        if flags['conn']: self._psqldisconnect()

        # before shutting down nidus & postgresql, confirm auto shutdown is enabled
        if not self._conf['policy']['shutdown']: return

        # shutdown nidus
        flags = bits.bitmask_list(_STATE_FLAGS_,self._state)
        if flags['nidus']:
            # do we have a password
            if not self._pwd:
                pwd = self._getpwd()
                if pwd is None:
                    self.logwrite("Password entry canceled. Cannot continue",gui.LOG_WARN)
                    return
                self._pwd = pwd

            self.logwrite("Stopping Nidus...",gui.LOG_NOTE)
            if not stopnidus(self._pwd):
                self.logwrite("Failed to stop Nidus",gui.LOG_WARN)
            else: self.logwrite("Nidus stopped")

        # shutdown postgresql (check first if polite)
        if self._conf['policy']['polite'] and self._bSQL: return
        flags = bits.bitmask_list(_STATE_FLAGS_,self._state)
        if flags['store']:
            # do we have a password
            if not self._pwd:
                pwd = self._getpwd()
                if pwd is None:
                    self.logwrite("Password entry canceled. Cannot continue",gui.LOG_WARN)
                    return
                self._pwd = pwd

            self.logwrite("Stopping PostgreSQL...",gui.LOG_NOTE)
            if not stoppsql(self._pwd):
                self.logwrite("Failed to stop PostgreSQL",gui.LOG_WARN)
            else: self.logwrite("PostgreSQL stopped")

        self._updatestate()
        self._menuenable()

    def _psqldisconnect(self):
        """ disconnect from postgresl """
        self.logwrite("Disconnecting from Nidus datastore...",gui.LOG_NOTE)
        self._conn.close()
        self._conn = None
        self._setstate(_STATE_CONN_,False)
        self.logwrite("Disconnected from Nidus datastore")

    def _startsensor(self):
        """ starts the DySKT sensor """
        flags = bits.bitmask_list(_STATE_FLAGS_,self._state)
        if flags['store'] and flags['nidus'] and not flags['dyskt']:
            # do we have a password
            if not self._pwd:
                pwd = self._getpwd()
                if pwd is None:
                    self.logwrite("Password entry canceled. Cannot continue",
                                  gui.LOG_WARN)
                    return
                self._pwd = pwd

            # start the sensor
            try:
                self.logwrite("Starting DySKT...",gui.LOG_NOTE)
                cmdline.service('dysktd',self._pwd)
                time.sleep(0.5)
                if not cmdline.dysktrunning(wraith.DYSKTPID): raise RuntimeError('unknown')
            except RuntimeError as e:
                self.logwrite("Error starting DySKT: %s" % e,gui.LOG_ERR)
            else:
                self.logwrite("DySKT Started")
                self._setstate(_STATE_DYSKT_)

    def _stopsensor(self):
        """ stops the DySKT sensor """
        flags = bits.bitmask_list(_STATE_FLAGS_,self._state)
        if flags['dyskt']:
            # do we have a password
            if not self._pwd:
                pwd = self._getpwd()
                if pwd is None:
                    self.logwrite("Password entry canceled. Cannot continue",
                                  gui.LOG_WARN)
                    return
                self._pwd = pwd

            # stop the sensor
            try:
                self.logwrite("Shutting down DySKT...",gui.LOG_NOTE)
                cmdline.service('dysktd',self._pwd,False)
            except RuntimeError as e:
                self.logwrite("Error shutting down DySKT: %s" % e,gui.LOG_ERR)
            else:
                self._setstate(_STATE_DYSKT_,False)
                self.logwrite("DySKT shut down")

    def _getpwd(self):
        """ prompts for sudo password until correct or canceled"""
        dlg = gui.PasswordDialog(self)
        try:
            # test out sudo pwd
            while not cmdline.testsudopwd(dlg.pwd):
                self.logwrite("Bad password entered. Try Again",gui.LOG_ERR)
                dlg = gui.PasswordDialog(self)
            return dlg.pwd
        except AttributeError:
            return None # canceled

if __name__ == '__main__':
    # create arg parser and parse arguments
    ap = argparse.ArgumentParser(description="Wraith-rt %s" % wraith.__version__)
    ap.add_argument('-p','--pwd',help="Sudo Password")
    ap.add_argument('-s','--start',help="Start with options = one of {nogui|gui|all}")
    ap.add_argument('-d','--stop',action='store_true',help="Stops all found services")
    ap.add_argument('-x','--exclude',action='store_true',help="Does not stop PostgreSQL")
    ap.add_argument('-v','--version',action='version',version="Wraith-rt %s" % wraith.__version__)
    args = ap.parse_args()
    pwd = args.pwd
    sopts = args.start
    stop = args.stop
    exclude = args.exclude

    # default is start nogui
    if sopts is None and stop == False: sopts = 'gui'

    # make sure both start and stop have not both been specified
    if sopts is not None and stop == True: ap.error("Cannot specify both start and stop")

    # check for pwd if start nogui all
    if (sopts == 'nogui' or sopts == 'all') and pwd is None:
        ap.error("Sudo password must be present when starting with nogui or all")

    # check for pwd if stop
    if stop and pwd is None: ap.error("Sudo password must be present when stopping")

    # check for valid pwd if present
    if pwd is not None and not cmdline.testsudopwd(pwd):
        ap.error("Sudo password was incorrect")

    # stop services - assuming no gui
    if stop:
        # stop DySKT, then Nidus, then PostgreSQL
        sd = sn = sp = True
        if cmdline.dysktrunning(wraith.DYSKTPID):
            ret = 'ok' if stopdyskt(pwd) else 'fail'
            print "Stopping DySKT\t\t\t\t[%s]" % ret
        if cmdline.nidusrunning(wraith.NIDUSPID):
            ret = 'ok' if stopnidus(pwd) else 'fail'
            print "Stopping Nidus\t\t\t\t[%s]" % ret
        if cmdline.runningprocess('postgres') and not exclude:
            ret = 'ok' if stoppsql(pwd) else 'fail'
            print "Stopping PostgreSQL\t\t\t[%s]" % ret
        sys.exit(0)

    # start specified
    if sopts == 'nogui':
        # start postgresql (if needed), nidus and dyskt
        if not cmdline.runningprocess('postgres'):
            ret = 'ok' if startpsql(pwd) else 'fail'
            print "Starting PostgreSQL\t\t\t[%s]" % ret
        else:
            print "PostgreSQL already running"
        if not cmdline.nidusrunning(wraith.NIDUSPID):
            ret = 'ok' if startnidus(pwd) else 'fail'
            print "Starting Nidus\t\t\t\t[%s]" % ret
        else:
            print "Nidus already running"
        if not cmdline.dysktrunning(wraith.DYSKTPID):
            # TODO: will fail because dyskt is running as root
            ret = 'ok' if startdyskt(pwd) else 'fail'
            print "Starting DySKT\t\t\t\t[%s]" % ret
        else:
            print "DySKT already Running"
        sys.exit(0)
    else:
        print 'simulating start'
        sys.exit(0)
        # start gui
        start = True if sopts == 'all' else False
        t = tk.Tk()
        s = ttk.Style()
        if 'clam' in s.theme_names(): s.theme_use('clam')
        s.configure("red.Horizontal.TProgressbar",foreground='red',background="red")
        try:
            WraithPanel(t,pwd,start).mainloop()
        except Exception as e:
            fout = open('wraith.log','a')
            fout.write("%s\n" % e)
            fout.close()

    sys.exit(0)