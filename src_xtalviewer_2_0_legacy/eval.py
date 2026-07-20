import os, sys
import glob
import getpass
#from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtWidgets import *
#from PyQt5.QtGui import QIcon, QImage, QPixmap, QPalette, QPainter
from PyQt5.QtGui import *
from PyQt5.QtPrintSupport import QPrintDialog, QPrinter
#from PyQt5.QtCore import Qt
from PyQt5.QtCore import *
import PyQt5.Qt

import csv, math, time
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
#from mpl_toolkits.mplot3d import Axes3D
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from utils import misc, func, chem
from utils.client import getMxlive, putMxlive

class index(QMainWindow):
    
    def __init__(self):
        super().__init__()
        main = MyApp(self)
        self.setCentralWidget(main)

        self.setWindowTitle('Xtal Viewer')
        self.setWindowIcon(QIcon('/usr/local/XtalViewer/1.0/img/fairy_03.png'))
        self.weight = 800
        self.height = 500
        self.left = 500
        self.top  = 0
        self.setGeometry(self.left, self.top, self.weight, self.height)
        
        
        self.statusbar = self.statusBar()
        self.setMouseTracking(True)

        self.show()

    #def mouseMoveEvent(self, event):
        #text = "Mousr Position: x={0}, y={0}, global = {2},{3}".format(event.x(), event.y(), event.globalX(), event.globalY())
        #self.statusbar.showMessage(text)


class MyApp(QWidget):

    def __init__(self, window=None):
        super().__init__()
        self.initUI()
        self.window = window
        #self.resizeEvent = self.imageResizeEvent
        #try:
        #    screen =  misc.configToDict('config/screen')
        #    self.left = 500
        #    self.top  = 0
        #    self.weight = int(screen['screenWidth'])
        #    self.height = int(screen['screenHeight'])
        #    self.setFixedWidth(self.weight)
        #    self.setFixedHeight(self.height)
        #except FileNotFoundError:
        #    print("No screen Configure")
        #except KeyError:
        #    print("Incomplete configure")
        #except ValueError:
        #    print("Wrong configuration value")

    def initUI(self):
        #self.setWindowTitle('My First Application')
        #self.setWindowIcon(QIcon('/usr/local/XtalViewer/1.0/img/fairy_03.png'))

        ###[General Definitions]
        self.pathway     = {'rmserver':'/smbmount/rmserver/RockMakerStorage/WellImages',\
                            'echo650' :'/smbmount/echo650',\
                            'shifter1':'/smbmount/shifter1',\
                            'shifter2':'/smbmount/shifter2',\
                            'cellar'  :'/data/users/{0}/FBDD/record'.format(misc.getUsername())}
    
        self.cellarPath   = "/data/users/{0}/FBDD/record".format(misc.getUsername())
        self.accpath      = {'icons'   :'/usr/local/XtalViewer/1.0/img/icons'}
        self.iconColor    = '333333'
        self.ImageTypeDic = {'Visible':'1', 'Contrast':'3?', 'HighRes':'5', 'Polarize':'8'}
        self.plateGrid    = {"row" : ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'], \
                             "col" : [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], \
                             "baby": ['a','c','d']}
        self.wellRadius  = 1400       #A BabyDrop Radius  1.4mm (1400um)
        self.guideRadius = 485        #Radius of Centering Guide Circle (485px)
        self.guideShift0 = [150, 50] 
        self.guideShift1 = [150, 50]
        #self.colOffset   = [5.2, -1.7] #To Calibrate Horizontal direction motor error
        #self.rowOffset   = [2.2, 3.7]  #To Calibrate  Vertical  direction motor error
        self.colOffset = [0,0]
        self.rowOffset = [0,0]
        self.zeroPlate = '690'
        #LOCALTEST self.cellarPath   = "/usr/local/XtalViewer/1.0/data"


        ###Variables for [XtalViewer Configuration]
        self.ImageProfile = "profileID_1"
        self.ImageType    = QComboBox(self)
        self.PlateType    = QComboBox(self)
        self.xCenter  = QSlider(Qt.Horizontal)
        self.yCenter  = QSlider(Qt.Horizontal)
        self.imgScale = QDoubleSpinBox()         
        #self.targetSite = QSpinBox()
        #self.targetVol  = QLineEdit('0')

        ####Variables for [Save Personalized Configuration]
        self.ConfigPath    = "/data/users/{0}/FBDD/config".format(misc.getUsername())
        self.screenConfig0 = {'xShift':0, 'yShift':0, 'imgScale':40}
        self.screenConfig1 = {}
        self.OrgImage      = [0,0]
        #LOCALTEST self.ConfigPath = '/usr/local/XtalViewer/1.0/src/config/screen'

        ###Variables for [Object Plates]
        self.objectPlates = QLineEdit()
        self.tableview = QTableView()
        self.tableview_model = QStandardItemModel()
        self.tableview.setSortingEnabled(True)
        self.checkedPlates = []
        self.plateCatalogue = {}

        ###Variables for [Plate Navigator]
        self.currPlate_view = QLineEdit()
        self.currWell_view  = QLineEdit()
        self.currSelect_view = QLabel('0')
        self.currentPlate = 'none'
        self.currPlate_info = []
        self.currentWell  = 1


        self.Targeting = {}
        #scale who are you?
        self.scale = [False, []]
        self.temp = [0,0]



        ###Variables for [Draw on the Crystal Viewer]
        self.fig = plt.Figure()
        self.canvas = FigureCanvas(self.fig)        


        ###Variables for [All ProjectTypes]
        self.NewExpID = 'none'
        self.expOnWeb = getMxlive.expOnMxlive()[::-1]
        self.planType = 'free'                         # one of  free/pretest/screen

        ###Variavles for [ProjectType: FreeTarget]
        self.targetSite = QSpinBox()
        self.targetVol  = QLineEdit('0')


        ###Variables for [ProjectType: Evaluation]
        self.incMinute  = [0, 30, 60, 120]  #Minute
        #self.incMinute = [QLineEdit('0'), QLineEdit('30'), QLineEdit('60'), QLineEdit('120')]
        self.defaultSV  = [10,25,50]
        self.defaultCP  = [80,100]
        self.concCondition = []
        #6 = len(self.defaultSV) * len(self.DefaultCP)
        #Move toward horizontal direction
        #self.keys = ['{0},{1}'.format(i,j) for j in range(6) for i in range(len(self.incMinute))]
        #Move toward vertical direction
        self.keys = ['{0},{1}'.format(i,j) for i in range(len(self.incMinute)) for j in range(6)]
        self.colors  = [['#7ecfc6', '#4bbfcc', '#10a8bf', '#0174ab', '#005584', '#20607c'],\
                        ['#eff964', '#eef244', '#e7e900', '#ddd101', '#bfb900', '#838c00'],\
                        ['#f2a5d1', '#ec79bf', '#e651ad', '#e5209c', '#d3048c', '#a5035e'],\
                        ['#b9b9b9', '#a2a2a2', '#878787', '#4c4c4c', '#4a4a4a', '#505050']]

        self.EvalueTargets = {}

        self.tableview2 = QTableView()
        self.tableview_model2 = QStandardItemModel()
        self.EvalueSolvType  = QComboBox()
        self.EvalueReplicaNo = QSpinBox()
        self.EvalueTargeting = {}

        self.EvaluePlanned = False
        self.EvalueProtein = QLineEdit('[Protein Name]')
        self.EvalueExpList = ['New'] + [item for item in self.expOnWeb if item.startswith('Pre')]
        self.EvalueExpID   = 'pretest_expid'
        #self.EvalueLib     = QComboBox(self)
        #self.EvalueSolvVol        = QDoubleSpinBox()
        self.EvalueSolvParts      = QDoubleSpinBox()
        self.EvalueSolvVolumes = []  
        self.EvalueConditions     = []   #Corresponding to ChemLibrary list
        self.EvalueCurrCondition  = 'none'
        self.EvalueSamples        = [] 
        #self.EvalueSolvLib_info  = []
        #self.EvalueSolvPlates    = []   #I cannot sure that it is necessary
        self.EvalueSolvWell      = 1
        self.EvalueSolvWell_fin  = 1
        #self.EvalueCryoPlate   = QLineEdit('Labcyte00')
        #self.EvalueCryoWell    = QLineEdit('A1')
        #self.EvalueCryoWells   = []


        self.EvalueCryoWellVol = 28000
        #self.EvalueCryoVol        = QDoubleSpinBox()
        self.EvalueCryoParts      = QDoubleSpinBox()
        self.EvalueCryoVol        = QDoubleSpinBox()
        self.EvalueCryoVolumes = [] 
        self.EvalueCryoCnt     = 1
        
        self.EvaluePlateCnt = {}

        self.xtalsToEvaluate = []




        ###Variables for [ProjectType: Experiment]
        self.libraryPath = '/usr/local/XtalViewer/1.0/chems'
        self.chemsPath = "{0}/chems".format(self.cellarPath)
        
        self.ScreenPlanned = False
        self.ScreenProtein = QLineEdit('[Protein Name]')
        #self.ScreenExpList = ['New'] + self.expOnWeb
        self.ScreenExpList = ['New'] +[item for item in self.expOnWeb if item.startswith('FragSc')]
        self.ScreenExpID   = QComboBox(self)
        self.SelectLib     = QComboBox(self)
        self.ScreenChemVol       = QDoubleSpinBox()
        self.ScreenChemParts     = QDoubleSpinBox()
        self.ScreenChemLib_info  = []
        self.ScreenChemPlates    = []   #I cannot sure that it is necessary
        self.ScreenChemWell_st   = QSpinBox()
        self.ScreenChemWell_st.setValue(1)
        self.ScreenChemWell      = 1
        self.ScreenChemWell_fin  = 1
        self.ScreenCryoPlate   = QLineEdit('CryoPlate')
        self.ScreenCryoWell    = QLineEdit('A1')
        self.ScreenCryoWells   = []
        self.ScreenCryoWellVol = 28000
        self.ScreenCryoVol     = QDoubleSpinBox()
        self.ScreenCryoParts   = QDoubleSpinBox()
        self.ScreenCryoVol     = QDoubleSpinBox()
        self.ScreenCryoCnt     = 1 
        self.xtalsToScreen = []

        grid = QGridLayout()
        grid.addWidget(self.ControlPanel(), 0,0)
        grid.addWidget(self.ViewPanel(),     0,1)
        self.setLayout(grid)


       
    def ControlPanel(self):
        ControlPanel = QWidget()
        grid = QGridLayout()
        grid.addWidget(self.Configuration(), 0,0)           
        grid.addWidget(self.imageAdjustment(),  1,0)
        grid.addWidget(self.ObjectPlates(),  2,0)
        #grid.setRowStretch(3,0)
        grid.setAlignment(Qt.AlignLeft)
        ControlPanel.setLayout(grid)
        return ControlPanel

    def Configuration(self):
        groupbox = QGroupBox('Xtal Viewer Configure')
        grid = QGridLayout()

        imgTypes = sorted(self.ImageTypeDic.items(), key=lambda item:item[1])
        for key,val in imgTypes:
            self.ImageType.addItem(key)
        self.ImageType.move(10,0)
        self.ImageType.currentTextChanged.connect(self.selectImageType)        
        self.PlateType.addItem('SwissCI-MRC-3d')
        self.PlateType.addItem('SwissCI-MRC-2d')
        self.targetSite.setRange(1, 10)
        self.targetSite.setValue(1)
        self.targetSite.valueChanged.connect(self.targetSites)

        grid.addWidget(QLabel('ImageType'),      0,0)
        grid.addWidget(self.ImageType,           0,1)
        grid.addWidget(QLabel('PlateType'),      1,0)
        grid.addWidget(self.PlateType,           1,1)
        #grid.addWidget(QLabel('Targets/Well'),   2,0)
        #grid.addWidget(self.targetSite,          2,1)
        #grid.addWidget(QLabel('Target Vol'),     3,0)
        #grid.addWidget(self.targetVol,           3,1)

        groupbox.setMaximumHeight(200)
        groupbox.setMaximumWidth(250)
        groupbox.setLayout(grid)
        return groupbox

    def selectImageType(self):
        self.ImageProfile = "profileID_{0}".format(self.ImageTypeDic[self.ImageType.currentText()])
        print('[ViewerSetup] ',self.ImageProfile)

    def targetSites(self):
        site = self.targetSite.value()
        temp = self.targetVol.text().split(',')
        volume = []
        for item in temp:
            if int(item): volume.append(item)
            else: pass
        if site == len(volume): pass
        elif site < len(volume):
            while len(volume) > site:
                del volume[-1]
            self.targetVol.setText(','.join(volume))
        else:
            while len(volume) < site:
                volume.append('0')
            self.targetVol.setText(','.join(volume))


    def imageAdjustment(self):
        groupbox = QGroupBox("Viewer Adjustment")
        grid = QGridLayout()
        screenConfig = '{0}/screen'.format(self.ConfigPath)
        if not os.path.isfile(screenConfig):
            self.defaultScreenSetting()
        try:
            self.screenConfig1 = misc.configToDict(screenConfig)
            xShift0 = int(self.screenConfig1['xShift'])
            yShift0 = int(self.screenConfig1['yShift'])
            imgScale0 = float(self.screenConfig1['imgScale'])
            self.guideShift1[0] = self.guideShift0[0] + xShift0
            self.guideShift1[1] = self.guideShift0[1] + yShift0
            print('[ViewerSetup] User-defined Configuration was found')
        except FileNotFoundError:
            self.defaultScreenSetting()
            print('[ViewerSetup] No Screen Configuration')
            print('[ViewerSetup] Viewer Setup as Default opt')
        except KeyError:
            self.defaultScreenSetting()
            print('[ViewerSetup] [!] Incomplete Configuration')
            print('[ViewerSetup] Viewer Setup as Default opt')
        except ValueError:
            self.defaultScreenSetting()
            print('[ViewerSetup] [!] Wrong Configuration')
            print('[ViewerSetup] Viewer Setup as Default opt')
        finally:
            print('[ViewerSetup] CircularGuideCenter : X-{0}px, Y-{1}px @ScalFactor=1'\
                  .format(self.guideShift1[0]+self.guideRadius,self.guideShift1[1]+self.guideRadius))
            print('[ViewerSetup] Image Magnification : {0}%'.format(self.screenConfig1['imgScale']))

        self.xCenter.setRange(-50,50)
        self.xCenter.move(1,1)
        self.xCenter.setTickPosition(QSlider.TicksBothSides)
        self.xCenter.setTickInterval(5)
        self.xCenter.setSingleStep(1)
        self.xCenter.setValue( int(self.screenConfig1['xShift']) )
        self.xCenter.sliderReleased.connect(self.moveCenterX)

        self.yCenter.setRange(-50,50)
        self.yCenter.move(1,1)
        self.yCenter.setTickPosition(QSlider.TicksBothSides)
        self.yCenter.setTickInterval(5)
        self.yCenter.setSingleStep(1)
        self.yCenter.setValue( int(self.screenConfig1['yShift']) )
        self.yCenter.sliderReleased.connect(self.moveCenterY)
        
        self.imgScale.setRange(40, 120)
        self.imgScale.setSuffix('%')
        self.imgScale.setSingleStep(10)
        self.imgScale.setDecimals(0)
        self.imgScale.setValue( float(self.screenConfig1['imgScale']) )
        self.imgScale.valueChanged.connect(self.changeImgScale)
        #self.imgScale.editingFinished.connect(self.changeImgScale)
        restoreButton = QPushButton('Restore')
        restoreButton.clicked.connect( self.defaultScreenSetting )
        fixCurrButton = QPushButton('Save')
        fixCurrButton.clicked.connect(self.fixScreenSetting )
        self.preScale = self.imgScale.value()

        self.resetScreenSetting()

        grid.addWidget(QLabel("Center X-axis"),    0,0,1,2)
        grid.addWidget(self.xCenter,               0,2,1,4)
        grid.addWidget(QLabel("Center Y-axis"),    1,0,1,2)
        grid.addWidget(self.yCenter,               1,2,1,4)
        grid.addWidget(QLabel("Image Resize"),     2,0,1,2)
        grid.addWidget(self.imgScale,              2,2,1,4)
        grid.addWidget(restoreButton,              3,0,1,3)
        grid.addWidget(fixCurrButton,              3,3,1,3)       

        groupbox.setMaximumWidth(250)
        groupbox.setMaximumHeight(200)
        groupbox.setLayout(grid)      
        return groupbox 

    def defaultScreenSetting(self):
        self.screenConfig1 = self.screenConfig0
        xShift = self.screenConfig1['xShift']
        yShift = self.screenConfig1['yShift']
        imgScale = self.screenConfig1['imgScale']
        self.guideShift1[0] = self.guideShift0[0] + xShift
        self.guideShift1[1] = self.guideShift0[1] + yShift
        self.resetScreenSetting()
        self.fixScreenSetting()

    def moveCenterX(self):
        prevCenterX  =  self.guideShift1[0]
        movingStep   = self.xCenter.value()
        center = self.guideShift0[0] + self.xCenter.value()
        self.guideShift1[0] = center
        print('[ViewerSEtup] centerX: {0} -> {1}'.format( prevCenterX, self.guideShift1[0]))
        try:
            self.loadImage()
        except IndexError as e:
            pass
    def moveCenterY(self):
        prevCenterY = self.guideShift1[1]
        movingStep  = self.yCenter.value()
        center = self.guideShift0[1] + self.yCenter.value()
        self.guideShift1[1] = center
        print('[ViewerSEtup] centerY: {0} -> {1}'.format(prevCenterY, self.guideShift1[1]))
        try:
            self.loadImage()
        except IndexError as e:
            pass

    def changeImgScale(self):
        try:
            self.loadImage()
        except:
            pass
        finally:
            print('[ImageScale] Current Window {0} x {1}'.format( self.window.sizeHint().width(), self.window.size().height() ))
            print('[ImageScale] Magnification {0}% > {1}%'.format(self.preScale, self.imgScale.value()))
            dImgScale = self.imgScale.value() - 30
            dWidth = self.OrgImage[0] * dImgScale/100
            dHeight = self.OrgImage[1] * dImgScale/100
            if self.imgScale.value() - self.preScale >=0:
                self.window.resize(701+dWidth, 658+dHeight)
            else:
                #self.window.resize(701+dWidth, 658+dHeight)
                self.window.resize(0,0)
                self.window.resize(701+dWidth, 658+dHeight)
            self.preScale = self.imgScale.value()

    def resizeWindow(self):
        self.window.resize(self.temp[0],self.temp[1])

    def resetScreenSetting(self):
        self.xCenter.setValue( int(self.screenConfig1['xShift']) )
        self.yCenter.setValue( int(self.screenConfig1['yShift']) )
        self.imgScale.setValue( float(self.screenConfig1['imgScale']) )
 
    def fixScreenSetting(self):
        if not os.path.isdir(self.ConfigPath):
            misc.createFolder(self.ConfigPath)
        #print(self.window.sizeHint().width(), self.window.size().height())
        screenConfig = '{0}/screen'.format(self.ConfigPath)
        f = open(screenConfig, 'w')
        f.write( "xShift:{0}\n".format( str(self.xCenter.value()) ) ) 
        f.write( "yShift:{0}\n".format( str(self.yCenter.value()) ) )
        f.write( "imgScale:{0}\n".format( str(self.imgScale.value()) ) )
        #f.write("screenWidth:{0}\n".format(str(self.window.sizeHint().width())))
        #f.write("screenHeight:{0}\n".format(str(self.window.sizeHint().height())))
        #f.write( "screenWidth:{0}\n".format(str(self.width()) ) )
        #f.write( "screenHeight:{0}\n".format( str(self.height()) ) )
        f.close()
        print('[ViewerSetup] Current X,Y/ImgScale are Fixed')


    def ObjectPlates(self):
        groupbox = QGroupBox('Object Plates')
        grid = QGridLayout()

        enterload = QPushButton('Load', self)
        enterload.clicked.connect(self.LoadPlates)
        self.objectPlates.returnPressed.connect(self.LoadPlates)
        self.tableview.setModel(self.tableview_model)
        self.tableview.setSelectionBehavior(QAbstractItemView.SelectRows)
        #Plate list DoubleClick Event
        self.tableview.doubleClicked.connect( self.setCurrentPlate )

        ranker = QPushButton('Ranker')
        ranker.clicked.connect( self.zero )
        refresh = QPushButton('Refresh')
        refresh.clicked.connect( self.refreshPlates )
        clear = QPushButton('Clear')
        clear.clicked.connect( self.clearPlates )
        backdrop = QPushButton('Reset Backdrop Regions')
        backdrop.clicked.connect( self.zero )

        grid.addWidget(QLabel('Plates ID'), 0,0)
        grid.addWidget(self.objectPlates,   0,1)
        grid.addWidget(enterload,           0,2)
        grid.addWidget(self.tableview,      1,0,1,3)
        grid.addWidget(ranker,              2,0)
        grid.addWidget(refresh,             2,1)
        grid.addWidget(clear,               2,2)
        grid.addWidget(backdrop,            3,0,1,3)

        groupbox.setMaximumWidth(250)
        groupbox.setLayout(grid)
        return groupbox

    def LoadPlates(self):
        tempList = []
        wrongPlates = []
        if self.objectPlates.text():
            #Concerning
            tempList = self.objectPlates.text().split(',')
            for item in tempList:
                pID = item.strip()
                self.plateCatalogue[pID] = func.checkPlates(pID, self.ImageProfile, self.Targeting, self.pathway['rmserver'],  self.cellarPath)
            tempDict = self.plateCatalogue
            self.plateCatalogue, wrongPlates = func.CatalogueThinning(tempDict)
            self.refreshPlates()

            if len(wrongPlates) > 0:
                react = QMessageBox.warning(self, "Check plateID", '\n'.join(wrongPlates), QMessageBox.Yes)
                print('[ Warning ] Invalid Plates')
                if react == QMessageBox.Yes:
                    print('ok')
                else: pass
            else: pass
        else: 
            pass

    def clearPlates(self):
        self.tableview_model.removeRows(0, self.tableview_model.rowCount())
        self.tableview_model.removeColumns(0, self.tableview_model.columnCount())
        self.plateCatalogue.clear()
        self.checkedPlates.clear()
        self.Targeting.clear()

    def refreshPlates(self):
        self.tableview_model.removeRows(0, self.tableview_model.rowCount())
        self.tableview_model.removeColumns(0, self.tableview_model.columnCount())
        #self.checkedPlates.clear()

        i = 0
        self.check = []


        for plate, state in sorted(self.plateCatalogue.items()):
           cbw = QCheckBox()
           if plate in self.checkedPlates:
               cbw.toggle()
           else: pass
           cellwidget = QWidget()
           layout = QHBoxLayout(cellwidget)
           layout.addWidget(cbw)
           layout.setAlignment(Qt.AlignCenter)
           layout.setContentsMargins(0, 0, 0, 0)
           cellwidget.setLayout(layout)
           self.check.append(cbw)
           self.checkedPlates.append(plate)
           cbw.stateChanged.connect(self.groupPlates)
           state['selected'] = len( [ key for key,val in self.Targeting.items() if key.startswith(plate) ] )
           column00 = QStandardItem()
           column01 = QStandardItem(plate)
           column02 = QStandardItem(state['rank'])
           column03 = QStandardItem(str(state['drops']))
           column04 = QStandardItem(str(state['selected']))
           self.tableview_model.setHorizontalHeaderLabels(['','plateID', 'Rank', 'Drops', 'Select'])
           #self.tableview.setIndexWidget(self.tableview_model.index(i, 0), cellwidget)
           self.tableview_model.setItem(i, 0, column00)
           self.tableview_model.setItem(i, 1, column01)
           self.tableview_model.setItem(i, 2, column02)
           self.tableview_model.setItem(i, 3, column03)
           self.tableview_model.setItem(i, 4, column04)
           self.tableview.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
           self.tableview.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
           self.tableview.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
           self.tableview.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
           self.tableview.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
           column01.setTextAlignment(Qt.AlignHCenter|Qt.AlignVCenter|Qt.AlignCenter)
           column02.setTextAlignment(Qt.AlignHCenter|Qt.AlignVCenter|Qt.AlignCenter)
           column03.setTextAlignment(Qt.AlignHCenter|Qt.AlignVCenter|Qt.AlignCenter)
           column04.setTextAlignment(Qt.AlignHCenter|Qt.AlignVCenter|Qt.AlignCenter)
           self.tableview.setIndexWidget(self.tableview_model.index(i, 0), cellwidget)
           i += 1
        self.tableview.setEditTriggers(QAbstractItemView.NoEditTriggers)

        try: 
            imgPath = self.currPlate_info[self.currentWell][-1]
            print('[CurrentImg] ImgPath: {}'.format(imgPath))
            print('[Image Load] Original Image : {0} x {1}'.format( self.OrgImage[0], self.OrgImage[1] ))
            #print(self.currPlate_info[self.currentWell])
            # ['1', '700', 'A01a', 'wellNum_1', '1', '2021-09-11 23:14:33.976878', \
            #  '/smbmount/rmserver/RockMakerStorage/WellImages/700/plateID_700/batchID_3553/wellNum_1/profileID_1/d1_r70821_ef.jpg']
            print('[Image Load] ScaleFactor = {0}'.format( self.imgScale.value()/100 ) )
            print('[Image Load] Resize to {0} x {1}'.format(self.ImgWidth1, self.ImgHeight1))
        except IndexError as e:
            print('[Load Plate] Any plate did not selected')



    def groupPlates(self):
        if self.plateCatalogue:
            for i, checkbox in enumerate(self.check):
                pID = self.tableview_model.item(i,1).text()
                if checkbox.isChecked():
                    if not pID in self.checkedPlates:
                        self.checkedPlates.append(pID)
                else:
                    if pID in self.checkedPlates:
                        self.checkedPlates.remove(pID)
            temp = sorted(list(set(self.checkedPlates)))
            self.checkedPlates = temp
            print('[SelectPlates] ', self.checkedPlates)
        else: pass

    def setCurrentPlate(self):
        row = self.tableview.currentIndex().row()
        self.currentPlate = self.tableview_model.item(row, 1).text()
        currPlate_infoPath = "{0}/{2}_pID{1}_info.csv".format(self.cellarPath, self.currentPlate, misc.getUsername())
        self.currPlate_info = misc.csvToList(currPlate_infoPath)
        #for row in self.currPlate_info:
            #print('[rows]', row)
        self.currentWell = 1
        self.drawGraph()
        self.currPlate_view.setText(self.currentPlate)
        #self.currWell_view.setText(self.currPlate_info[self.currentWell][2])
        self.setCurrentWell()

    def setCurrentWell(self):
        #print(self.currPlate_info[self.currentWell])
        # ['1', '700', 'A01a', 'wellNum_1', '1', '2021-09-11 23:14:33.976878', \
        #  '/smbmount/rmserver/RockMakerStorage/WellImages/700/plateID_700/batchID_3553/wellNum_1/profileID_1/d1_r70821_ef.jpg']
        try:
            self.currWell_view.setText(self.currPlate_info[self.currentWell][2])
            self.loadImage()
            print('[CurrentWell] pID{0} {1}'.format(self.currentPlate, self.currPlate_info[self.currentWell][2]))
        except IndexError: pass 
        print('[setwell]', self.planType)
        if self.planType == 'pretest':
            targetsInThisPlate = 0
            print('here', self.EvalueTargeting)
            for conkey, targets in self.EvalueTargeting.items():
                print(conkey, len(targets), targets)
                targetsInThisPlate += len( [ key for key,val in targets.items() if key.startswith(self.currentPlate) ] )
        else:
            targetsInThisPlate = len( [ key for key,val in self.Targeting.items() if key.startswith(self.currentPlate) ] )
        self.currSelect_view.setText(str(targetsInThisPlate))
        print(targetsInThisPlate)

    def setFollowPlate(self):
        currPlate_infoPath = "{0}/{2}_pID{1}_info.csv".format(self.cellarPath, self.currentPlate, misc.getUsername())
        self.currPlate_info = misc.csvToList(currPlate_infoPath)
        self.currentWell = 1
        self.drawGraph()
        self.currPlate_view.setText(self.currentPlate)
        #self.currWell_view.setText(self.currPlate_info[self.currentWell][2])
        self.setCurrentWell()


    def ViewPanel(self):
        ViewPanel = QWidget()
        grid = QGridLayout()

        self.pathLineEdit = QLineEdit()
        self.imageLabel = QLabel()
        self.imageLabel.setPixmap(QPixmap())
        self.imageLabel.mousePressEvent = self.imageClickEvent

        self.OrgImgage = [0,0]
        self.ImgWidth0 = 612
        self.ImgHeight0 = 512
        self.ImgWidth1 = self.ImgWidth0
        self.ImgHeight0 = self.ImgHeight0
        self.dImgWidth = 0
        self.dImgHeight = 0
        self.imageLabel.setFixedWidth(self.ImgWidth0)
        self.imageLabel.setFixedHeight(self.ImgHeight0)
        
        self.pixmap = QPixmap()
        self.pixmap2 = QPixmap()

        grid.addWidget(self.Viewer(),        0,0)
        grid.addWidget(self.imgNavigator(),  1,0)
        grid.addWidget(self.chemNavigator(), 0,1, 2,1)
        #grid.setRowStretch(2,0)
        ViewPanel.setLayout(grid)

        grid.setAlignment(Qt.AlignLeft)
        return ViewPanel
    
    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Right:
            self.moveNextWell()
            print('next')
        elif e.key() == Qt.Key_Left:
            self.movePrevWell()
        elif e.key() ==Qt.Key_Space:
            print('space, next')
            self.moveNextWell()
        else: pass
    

    def imgNavigator(self):
        groupbox = QGroupBox()
        widget = QWidget()
        grid = QGridLayout()

        orderBy = QComboBox(self)
        orderBy.addItem('WellNo')
        orderBy.addItem('Score')

        self.currWell_view.returnPressed.connect(self.moveToWell)
        self.currPlate_view.returnPressed.connect(self.moveToPlate)

        buttonWidget = QWidget()
        buttonbox = QHBoxLayout()
        homeButton = QPushButton()
        prevButton = QPushButton()
        nextButton = QPushButton()
        lastButton = QPushButton()
        undoButton = QPushButton()
        tapeButton = QPushButton()

        homeButton.clicked.connect(self.moveFirstWell)
        prevButton.clicked.connect(self.movePrevWell)
        nextButton.clicked.connect(self.moveNextWell)
        lastButton.clicked.connect(self.moveLastWell)
        undoButton.clicked.connect(self.removeLastTarget)     
        #tapeButton.clicked.connect(self.tapeMeasure)
        
        homeButton.setIcon(QIcon('{0}/{1}_first.png'.format(self.accpath['icons'],self.iconColor)))
        prevButton.setIcon(QIcon('{0}/{1}_prev.png'.format(self.accpath['icons'],self.iconColor)))
        nextButton.setIcon(QIcon('{0}/{1}_next.png'.format(self.accpath['icons'],self.iconColor)))
        lastButton.setIcon(QIcon('{0}/{1}_end.png'.format(self.accpath['icons'],self.iconColor)))
        undoButton.setIcon(QIcon('{0}/{1}_eraser.png'.format(self.accpath['icons'],self.iconColor)))
        #tapeButton.setIcon(QIcon('{0}/{1}_ruler.png'.format(self.accpath['icons'],self.iconColor)))

        buttonbox.addWidget(homeButton)
        buttonbox.addWidget(prevButton)
        buttonbox.addWidget(nextButton)
        buttonbox.addWidget(lastButton)
        buttonbox.addWidget(undoButton)
        #buttonbox.addWidget(tapeButton)
        buttonWidget.setLayout(buttonbox)       
        buttonWidget.setMinimumHeight(37)
        saveButton = QPushButton("Targets in Current plate")
        saveButton.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_DialogSaveButton')))
        saveButton.clicked.connect(self.saveFreeTarget)

        grid.addWidget(self.canvas,              0,0,6,1)
        grid.addWidget(QLabel("Current Plate"),  0,1)
        grid.addWidget(self.currPlate_view,      0,2)
        grid.addWidget(QLabel("Current Well"),   1,1)
        grid.addWidget(self.currWell_view,       1,2)
        grid.addWidget(QLabel("Order by"),       2,1)
        grid.addWidget(orderBy,                  2,2)
        grid.addWidget(QLabel("Selected Well"),  4,1)
        grid.addWidget(self.currSelect_view,     4,2)
        grid.addWidget(buttonWidget,             3,1,1,2)
        grid.addWidget(saveButton,               5,1,1,2)
        grid.setColumnStretch(0,3)
        grid.setColumnStretch(1,0)
        grid.setColumnStretch(2,0)
        
        #PyQt5.QtCore.QSize(800, 669)
        groupbox.setMinimumWidth(self.OrgImage[0]*self.imgScale.value()/100)
        #groupbox.setMaximumWidth(self.OrgImgWidth*self.imgScale.value()/100)
        groupbox.setMinimumHeight(220)
        groupbox.setMaximumHeight(250)
        groupbox.setLayout(grid)

        return groupbox




    def saveFreeTarget(self):
        filesave = QFileDialog.getSaveFileName(self,"Save free target (csv)", self.pathway['cellar'], "CSV Files (*.csv)")
        print(filesave)
        if filesave[0] != '':
            targetList = func.freeTargetsToCSV(self.PlateType.currentText(), self.Targeting)
            with open(filesave[0], 'w') as f:
                csvWriter = csv.writer(f)
                for line in targetList:
                    csvWriter.writerow(line)


    def drawGraph(self):
        x = np.arange(0, 384, 1)
        #y1 = np.sin(x)
        #y2 = np.cos(x)
        y = []
        for i in range(0, 384):
            y.append(i)

        self.fig.clear()
        ax = self.fig.add_subplot(111)
        #ax.plot(x, y1, label="sin(x)")
        #ax.plot(x, y2, label="cos(x)", linestyle="--")
        ax.plot(x, y, label="lin", linestyle=":")


        ax.set_xlabel("x")
        ax.set_xlabel("y")

        ax.set_title(self.currentPlate)
        ax.legend()

        self.canvas.draw()


    def moveToPlate(self):
        switch = 0
        wannaGo = str( self.currPlate_view.text().strip() )
        plates = [ str(key) for key,val in self.plateCatalogue.items() ]
        if wannaGo in plates:
            self.currentPlate = wannaGo
            currPlate_infoPath = "{0}/{2}_pID{1}_info.csv".format(self.cellarPath, self.currentPlate, misc.getUsername())
            self.currPlate_info = misc.csvToList(currPlate_infoPath)
            print('[Load Plate] pID{0} selected'.format(self.currentPlate))
            self.currentWell = 1
            self.drawGraph()
            #self.currPlate_view.setText(self.currentPlate)
            self.setCurrentWell()
            switch = 1
        if switch == 0:
            message = "pID{0} is not Loaded".format( self.currWell_view.text() )
            react = QMessageBox.warning(self, "Check Plate ID", message , QMessageBox.Yes)
            if react == QMessageBox.Yes:
                pass

    def moveToWell(self):
        switch = 0
        for i,item in enumerate(self.currPlate_info):
            #print(i, item[2])
            if item[2] == str( self.currWell_view.text().strip() ):
                self.currentWell = i
                switch = 1
                self.setCurrentWell()
            else: pass
        #20220203 I think this is not necessary.
        if switch == 0:
            message = "{1} not found in pID{0}".format( self.currentPlate, self.currWell_view.text() )
            react = QMessageBox.warning(self, "Check Well ID", message , QMessageBox.Yes)
            if react == QMessageBox.Yes:
                pass

    def movePrevWell(self):
        if self.currentPlate != 'none':
            if self.currentWell == 1:
                print('[Note] First Well in The Plate')
            else:
                self.currentWell -= 1
                self.setCurrentWell()
                self.moveChemWell()
                print('[designation]', self.currPlate_info[self.currentWell])
        else: pass
            
    def moveNextWell(self):
        if self.planType == 'pretest':
            if self.currentWell == len( self.currPlate_info ) - 1:
                print('[Note] Last Well in the Plate')
            else: 
                self.currentWell += 1
                self.setCurrentWell()
        elif self.planType == 'screen':
            if self.currentPlate != 'none':
                if self.currentWell == len( self.currPlate_info ) - 1:
                    print('[Note] Last Well in the Plate')
                    nextplate = self.checkedPlates.index(self.currentPlate) + 1
                    if nextplate < len(self.checkedPlates):
                        self.currentPlate = self.checkedPlates[nextplate]
                        self.setFollowPlate()
                    else:
                        pass
                else:
                    self.currentWell += 1
                    self.setCurrentWell()
                    self.moveChemWell()
            else: pass
        else: 
            if self.currentWell == len( self.currPlate_info ) - 1:
                print('[Note] Last Well in the Plate')
            else:
                self.currentWell += 1
                self.setCurrentWell()



    def moveFirstWell(self):
        if self.currentPlate != 'none':
            if self.currentWell == 1:
                pass
            else: 
                self.currentWell = 1
                self.setCurrentWell()
                self.moveChemWell() #2022-02
        else: pass
    def moveLastWell(self):
        if self.currentPlate != 'none':
            if self.currentWell == len( self.currPlate_info ) - 1:
                pass
            else:
                self.currentWell = len( self.currPlate_info ) - 1
                self.setCurrentWell()
                self.moveChemWell() #2022-02
        else: pass

    def removeLastTarget(self):
        #self.Targeting = {'318_A02d': [[-807.0, -419.0, -111.80000000000001, -58.0], [-475.0, 325.0, -65.80000000000001, 45.0] ...]}
        if self.planType == 'screen' and self.currentPlate != 'none':
            try:
                imgKey  = '{0}_{1}'.format(self.currPlate_info[self.currentWell][1], self.currPlate_info[self.currentWell][2])
                #del self.Targeting[imgKey][-1]
                del self.Targeting[imgKey]
                self.Targeting = { k:v for k,v in self.Targeting.items() if len(v) != 0 }
                self.setCurrentWell()
                print('[RemoveTarget] {0} target(s) remained in pID{1} {2}'.format(len(self.Targeting[imgKey]), self.currentPlate, self.currPlate_info[self.currentWell][2]))
            except KeyError as e:
                print('[RemoveTarget] No target in pID{0} {1}'.format(self.currentPlate, self.currPlate_info[self.currentWell][2] ))
        elif self.planType == 'pretest' and self.currentPlate != 'none':
            try:
                imgKey  = '{0}_{1}'.format(self.currPlate_info[self.currentWell][1], self.currPlate_info[self.currentWell][2])
                #Get Current Condition key
                #try:
                #    i,j = self.getConditionKey()
                #    conKey = '{0},{1}'.format(i,j)
                #except: pass
                for key in self.keys:
                    if key in self.EvalueTargeting and imgKey in self.EvalueTargeting[key]:
                        print('[RemoveTarget] latest one in pID{0} {1}'.format(self.currentPlate, self.currPlate_info[self.currentWell][2]))
                        print(self.EvalueTargeting[key][imgKey])
                        #del self.EvalueTargeting[key][imgKey][-1]
                        del self.EvalueTargeting[key][imgKey]
                    else: pass
                #self.Targeting = { k:v for k,v in self.Targeting.items() if len(v) != 0 }
                self.fillSelectionMap()
                self.setCurrentWell()
            except KeyError as e:
                print('[RemoveTarget] No target in pID{0} {1}'.format(self.currentPlate, self.currPlate_info[self.currentWell][2] )) 
        else: pass

    def Viewer(self):
        groupbox = QGroupBox('Crystal View')
        grid = QGridLayout(groupbox)
        grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        grid.addWidget(self.imageLabel, 0,0)
        groupbox.setLayout(grid)
        #print( '->', self.groupbox.contentsMargins().bottom() )


        return groupbox

    def loadImage(self):
        imgPath = self.currPlate_info[self.currentWell][-1]
        imgKey  = '{0}_{1}'.format(self.currPlate_info[self.currentWell][1], self.currPlate_info[self.currentWell][2])
        try:
            i,j = self.getConditionKey()
            conKey = '{0},{1}'.format(i,j)
        except: pass
        #print(self.currPlate_info[self.currentWell])
        if imgPath != 'yet':
            #print('[CurrentImg] ImgPath: {}'.format(imgPath))
            image = QImage(imgPath)
            if image.isNull():
                QMessageBox.information(self, "Image Viewer", "Cannot load %s." % imgPath)
                return
            self.pathLineEdit.setText(imgPath)

            self.OrgImage[0] = image.width()
            self.OrgImage[1] = image.height()
            #print('[Image Load] Original Image : {0} x {1}'.format( image.width(), image.height() ))

            self.ImgWidth1 = self.OrgImage[0] * self.imgScale.value()/100
            self.ImgHeight1 = self.OrgImage[1] * self.imgScale.value()/100
            self.imageLabel.setFixedWidth(self.ImgWidth1)
            self.imageLabel.setFixedHeight(self.ImgHeight1)
            #print('[Image Load] ScaleFactor = {0}'.format( self.imgScale.value()/100 ) )
            #print('[Image Load] Resize to {0} x {1}'.format(self.ImgWidth1, self.ImgHeight1))

            self.pixmap = QPixmap(image)

            pen = QPen(QColor('#ffffff'), 5)
            pen2 = QPen(QColor('#ff0000'), 5)
            blue1   = QPen(QColor('#01567f'), 10)
            blue2   = QPen(QColor('#4cc4ff'),  3)
            violet1 = QPen(QColor('#744080'), 10)
            violet2 = QPen(QColor('#f5cdff'), 3)
            green1  = QPen(QColor('#017f7b'), 10)
            green2  = QPen(QColor('#4cfff9'), 3)
            painter = QPainter(self.pixmap)
            painter.setPen(pen)
            radius = self.guideRadius
            rowXoffset = int( self.currWell_view.text()[1:3] ) * self.colOffset[0] - self.colOffset[0]
            rowYoffset = int( self.currWell_view.text()[1:3] ) * self.colOffset[1] - self.colOffset[1]
            colXoffset = ( ord(self.currWell_view.text()[0]) - 65 ) * self.rowOffset[0] 
            colYoffset = ( ord(self.currWell_view.text()[0]) - 65 ) * self.rowOffset[1]
            Xoffset = rowXoffset + colXoffset
            Yoffset = rowYoffset + colYoffset

            center = [ self.guideShift1[0]+radius+Xoffset,  self.guideShift1[1]+radius+Yoffset ]
            cross = 30
            #r = QRectF(self.guideShift1[0], self.guideShift1[1], radius*2, radius*2)
            r = QRectF(center[0]-radius, center[1]-radius, radius*2, radius*2)
            painter.drawEllipse(r)
            painter.drawLine( center[0]-cross/2, center[1], center[0]+cross/2, center[1])
            painter.drawLine( center[0] ,center[1]-cross/2, center[0], center[1]+cross/2)
            painter.setPen(pen2)
            painter.drawPoint(center[0],center[1])


            try:
                if self.planType == 'pretest':
                    #current conditionKey... 
                    for key,val in self.EvalueTargeting.items():
                        print('[viewall]', key,val)
                        if imgKey in val:
                            for points in self.EvalueTargeting[key][imgKey]:
                                print('drawpoint', points)
                                #orgX = center[0]+points[3]/(self.imgScale.value()/100)
                                #orgY = center[1]+points[4]/(self.imgScale.value()/100)
                                orgX = center[0]+points[3]
                                orgY = center[1]+points[4]
                                #30 = 5nl, 45 = 10nl
                                radi = 30*2 /(self.imgScale.value()/100)
                                if points[0].startswith('solv'):
                                    painter.setPen(blue1)
                                    painter.drawPoint( orgX, orgY )
                                    painter.setPen(blue2)
                                    painter.drawEllipse( QRectF(orgX-radi/2, orgY-radi/2, radi, radi) )
                                elif points[0].startswith('cryo'):
                                    painter.setPen(violet1)
                                    painter.drawPoint( orgX, orgY )
                                    painter.setPen(violet2)
                                    painter.drawEllipse( QRectF(orgX-radi/2, orgY-radi/2, radi, radi) )
                                else:
                                    painter.setPen(green1)
                                    painter.drawPoint( orgX, orgY )
                                    painter.setPen(green2)
                                    painter.drawEllipse( QRectF(orgX-radi/2, orgY-radi/2, radi, radi) )
                else:
                    for points in self.Targeting[imgKey]:
                        #orgX = center[0]+points[3]/(self.imgScale.value()/100)
                        #orgY = center[1]+points[4]/(self.imgScale.value()/100)
                        print(points)
                        if points[0].startswith('post'): pass
                        else:
                            orgX = center[0]+points[3]
                            orgY = center[1]+points[4]
                            #30 = 5nl, 45 = 10nl
                            radi = 30*2 /(self.imgScale.value()/100)
                            if points[0].startswith('chem'):
                                painter.setPen(blue1)
                                painter.drawPoint( orgX, orgY )
                                painter.setPen(blue2)
                                painter.drawEllipse( QRectF(orgX-radi/2, orgY-radi/2, radi, radi) )
                            elif points[0].startswith('cryo'):
                                painter.setPen(violet1)
                                painter.drawPoint( orgX, orgY )
                                painter.setPen(violet2)
                                painter.drawEllipse( QRectF(orgX-radi/2, orgY-radi/2, radi, radi) )
                            else:
                                painter.setPen(green1)
                                painter.drawPoint( orgX, orgY )
                                painter.setPen(green2)
                                painter.drawEllipse( QRectF(orgX-radi/2, orgY-radi/2, radi, radi) )
            except KeyError as e:
                print(e)
                print('passing drawing')
            painter.end()

            self.pixmap = self.pixmap.scaledToWidth(self.ImgWidth1)
            self.pixmap = self.pixmap.scaledToHeight(self.ImgHeight1)
            self.imageLabel.setPixmap(self.pixmap)
        else: pass
            #image = QImage('/usr/local/XtalViewer/1.0/img/404_01.png')
            #self.ImgWidth1 = image.width() * self.imgScale.value()/100
            #self.ImgHeight1 = image.height() * self.imgScale.value()/100
            #self.imageLabel.setFixedWidth(self.ImgWidth1)
            #self.imageLabel.setFixedHeight(self.ImgHeight1)
            #print('resize to', self.ImgWidth1, self.ImgHeight1)
            #self.pixmap = self.pixmap.scaledToWidth(self.ImgWidth1)
            #self.pixmap = self.pixmap.scaledToHeight(self.ImgHeight1)
            #self.imageLabel.setPixmap(self.pixmap) 


    def doneMove(self):
        imgKey  = '{0}_{1}'.format(self.currPlate_info[self.currentWell][1], self.currPlate_info[self.currentWell][2])

        if self.planType == 'pretest':
            #Get Current Conditon Key, User could see it on colorful radio buttons.
            i,j = self.getConditionKey()
            conKey = '{0},{1}'.format(i,j)
            print('currentkey', i,j)
            if imgKey not in self.EvalueTargeting[conKey]: pass
            else:
                if len(self.EvalueTargeting[conKey][imgKey]) == self.EvalueSolvParts.value() + self.EvalueCryoParts.value():
                    eachSolvVol = misc.calEachSites( float(self.EvalueSolvVolumes[j].text()), int(self.EvalueSolvParts.value())) 
                    eachCryoVol = misc.calEachSites( float(self.EvalueCryoVolumes[j].text()), int(self.EvalueCryoParts.value()))
                    print(j, self.EvalueSolvVolumes[j].text())
                    sn = 0
                    cn = 0
                    for n, point in enumerate(self.EvalueTargeting[conKey][imgKey]):
                        print(len(point), point)
                        if len(point) == 5:
                            if point[0] == 'solvent':
                                point.append(eachSolvVol[sn])
                                sn += 1
                            elif point[0] == 'cryopro':
                                point.append(eachCryoVol[cn])
                                cn += 1
                    self.moveNextWell()
                    print('??', len(self.EvalueTargeting[conKey]))
                    if len(self.EvalueTargeting[conKey]) == self.EvalueReplicaNo.value():
                        print('grep all targets in this condition')
                        self.moveNextCondition() 
                        print(self.EvalueTargeting[conKey])
                    else: pass
        if self.planType == 'screen':
            if imgKey not in self.Targeting: pass
            else:
                #print(self.Targeting)
                #print(self.ScreenChemParts.value() + self.ScreenCryoParts.value())
                if len(self.Targeting[imgKey]) == self.ScreenChemParts.value() + self.ScreenCryoParts.value():
                    for i, item in enumerate(self.Targeting[imgKey]):
                        if len(item[0].split(' ')) == 5: pass
                        else:
                            temp = item[0]+' '+str(self.EachVol[i])
                            item[0] = temp
                    if self.ScreenChemParts.value() != 0:
                        self.Targeting[imgKey].append( [ 'postscript', self.ScreenChemLib_info[self.ScreenChemWell]['PlateID'],\
                                                               self.ScreenChemLib_info[self.ScreenChemWell]['PlateWell'],\
                                                               self.ScreenChemLib_info[self.ScreenChemWell]['ChemID'],\
                                                               self.ScreenChemLib_info[self.ScreenChemWell]['SMILE'],\
                                                               self.currPlate_info[self.currentWell][-1] ] )
                    else: pass
                    self.moveNextWell()
                    self.moveNextChemWell()
                else: pass



    def chemNavigator(self):
        #groupbox = QGroupBox(" ")
        widget = QWidget()
        grid = QGridLayout()
        self.view_chemLib     = QLabel()
        self.view_chemWell    = QLabel()
        self.view_chemID      = QLabel()
        self.view_chemFormula = QLabel()
        self.view_chemSMILE   = QLineEdit()
        self.view_chemConc    = QLabel()
        self.view_chemMW      = QLabel()
        self.view_chemSolvent = QLabel()
        self.view_chemVendor  = QLabel()    
        DBrecord = []
        self.noneChemical()

        self.CimgLabel = QLabel()
        self.CimgLabel.setPixmap(QPixmap())



        buttonWidget = QWidget()
        buttonbox = QHBoxLayout()
        workSheetButton = QPushButton("Work")
        #workSheetButton.released.connect(lambda: func.targetsToCSV(self.PlateType.currentText(),\
        #                                                           self.ScreenExpID.currentText(),\
        #                                                           self.ScreenExpList,\
        #                                                           self.ScreenProtein.text(),\
        #                                                           self.Targeting, self.pathway, self.xtalsToScreen))
        workSheetButton.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_DialogSaveButton')))
        workSheetButton.setIcon(QIcon('{0}/{1}_xtaldone.png'.format(self.accpath['icons'],self.iconColor)))
        workSheetButton.released.connect(self.workSheets)
        shft1ToWebButton = QPushButton("SHFT1")
        shft1ToWebButton.setIcon(QIcon('{0}/{1}_microscope.png'.format(self.accpath['icons'],self.iconColor)))
        shft1ToWebButton.released.connect(self.shft1Finish)
        shft2ToWebButton = QPushButton("SHFT2")
        shft2ToWebButton.setIcon(QIcon('{0}/{1}_microscope.png'.format(self.accpath['icons'],self.iconColor)))
        shft2ToWebButton.released.connect(self.shft2Finish)
        buttonbox.addWidget(workSheetButton)
        buttonbox.addWidget(shft1ToWebButton)
        buttonbox.addWidget(shft2ToWebButton)
        buttonWidget.setLayout(buttonbox)

        #grid.addWidget(self.ScreenPlans(),        0,0)
        grid.addWidget(self.projectType(),      0,0)
        #grid.addWidget(self.loadChemical(),     1,0)
        grid.addWidget(buttonWidget,            2,0)
        widget.setMaximumWidth(300)
        widget.setMinimumHeight(300)
        widget.setLayout(grid)
        return widget

        

    def workSheets(self):
        if self.planType == 'pretest':
            print('[checkEXPID]', self.EvalueExpID)
            jsonpath, self.xtalsToScreen = func.targetsToCSV2(self.PlateType.currentText(),\
                                               self.EvalueExpID,\
                                               self.EvalueSolvType.currentText(),\
                                               #self.EvalueExpList,\
                                               self.EvalueProtein.text(),\
                                               self.EvalueTargeting, self.pathway,\
                                               self.incMinute, self.EvalueConditions)
            for item in sorted(os.listdir(jsonpath)):
                if item.startswith(self.EvalueExpID) and item.endswith('json'):
                    jsonfile = '{0}/{1}'.format(jsonpath, item)
                    data = putMxlive.upload_labworks('BL-5C', jsonfile)
                    print('[uploadtoMxlive]',data)
                else: pass

        elif self.planType == 'screen':
            jsonpath, self.xtalsToScreen = func.targetsToCSV(self.PlateType.currentText(),\
                                               self.ScreenExpID.currentText(),\
                                               self.ScreenExpList,\
                                               self.ScreenProtein.text(),\
                                               self.Targeting, self.pathway)

            for item in sorted(os.listdir(jsonpath)):
                if item.startswith(self.ScreenExpID.currentText()) and item.endswith('json'):
                    jsonfile = '{0}/{1}'.format(jsonpath, item)
                    data = putMxlive.upload_labworks('BL-5C', jsonfile)
                    print(data)
                else: pass

        #when use free targets
        else:
            print("[Done] Cannot upload FreeTargets to mxlive") 

    def shft1Finish(self):
        jsonpath = func.shft1Done(self.xtalsToScreen, self.ScreenExpID.currentText(), self.ScreenProtein.text(),self.pathway)
        for item in sorted(os.listdir(jsonpath)):
            if item.startswith(self.ScreenExpID.currentText()) and item.endswith('json'):
                jsonfile = '{0}/{1}'.format(jsonpath, item)
                data = putMxlive.upload_labworks('BL-5C', jsonfile)
                print(data)
            else: pass
        
    def shft2Finish(self):
        jsonpath = func.shft2Done(self.xtalsToScreen, self.ScreenExpID.currentText(), self.ScreenProtein.text(),self.pathway)
        for item in sorted(os.listdir(jsonpath)):
            if item.startswith(self.ScreenExpID.currentText()) and item.endswith('json'):
                jsonfile = '{0}/{1}'.format(jsonpath, item)
                data = putMxlive.upload_labworks('BL-5C', jsonfile)
                print(data)
            else: pass


    def projectType(self):
        #typeTab = QWidget()
        #vbox = QVBoxLayout()
        tabs = QTabWidget()
        tabs.addTab(self.freetarget(), " Free  ")
        tabs.addTab(self.evaluation(), "Evaluation")
        tabs.addTab(self.experiment(), "Experiment")
        #vbox.addWidget(tabs)
        return tabs


    def freetarget(self):
       widget = QWidget()
       grid = QGridLayout()
       grid.addWidget(self.freePlans(),    0,0)
       grid.addWidget(self.loadSelectionMap(), 1,0)
       widget.setLayout(grid)
       return widget

    def evaluation(self):
       widget = QWidget()
       grid = QGridLayout()
       grid.addWidget(self.testPlans(),    0,0)
       grid.addWidget(self.loadSelectionMap(), 1,0)
       widget.setLayout(grid)

       return widget
    def experiment(self):
       widget = QWidget()
       grid = QGridLayout()
       grid.addWidget(self.ScreenPlans(),    0,0)
       grid.addWidget(self.loadChemical(), 1,0)
       widget.setLayout(grid)

       return widget

    def freePlans(self):
        groupbox = QGroupBox()
        grid = QGridLayout()

        sourcePlate = QComboBox()
        sourcePlate.addItem('SourcePlate01')
        sourcePlate.addItem('SourcePlate02')
        sourcePlate.addItem('SourcePlate03')
        sourceWell = QLineEdit()
        

        grid.addWidget(QLabel("SourcePlate"),  0,0)
        grid.addWidget(sourcePlate,            0,1)
        grid.addWidget(QLabel("SourceWell"),   1,0)
        grid.addWidget(sourceWell,             1,1)
        grid.addWidget(QLabel("Targets/Well"), 2,0)
        grid.addWidget(self.targetSite,        2,1)
        grid.addWidget(QLabel("Transfer Vol"), 3,0)
        grid.addWidget(self.targetVol,         3,1)
        groupbox.setLayout(grid)
        #self.loadChemImg()
        return groupbox


    def testPlans(self):
        #incMinute  = [0, 30, 60, 120]  #Minute
        #defaultSV  = [10,25,50]
        #defaultCP  = [80,100]
        #concCondition = len(defaultSV) * len(defaultCP)
        #colors  = [['#7ecfc6', '#4bbfcc', '#10a8bf', '#0174ab', '#005584', '#20607c'],\
        #           ['#eff964', '#eef244', '#e7e900', '#ddd101', '#bfb900', '#838c00'],\
        #           ['#f2a5d1', '#ec79bf', '#e651ad', '#e5209c', '#d3048c', '#a5035e'],\
        #           ['#b9b9b9', '#a2a2a2', '#878787', '#4c4c4c', '#4a4a4a', '#505050']]

        #widget = QWidget()
        groupbox = QGroupBox()
        grid = QGridLayout()

        self.EvalueSolvType.addItem('DMSO')
        self.EvalueSolvType.addItem('EthyleneGlycol')

        self.EvalueSolvParts.setRange(0,5)
        self.EvalueSolvParts.setSuffix('point(s)')
        self.EvalueSolvParts.setSingleStep(1)
        self.EvalueSolvParts.setDecimals(0)
        self.EvalueSolvParts.setValue( float(1) )

        self.EvalueCryoParts.setRange(0,10)
        self.EvalueCryoParts.setSuffix('point(s)')
        self.EvalueCryoParts.setSingleStep(1)
        self.EvalueCryoParts.setDecimals(0)
        self.EvalueCryoParts.setValue( float(0) )

        self.EvalueReplicaNo.setRange(1,6)
        self.EvalueReplicaNo.setValue(3)

        editButton = QPushButton("Edit")
        editButton.clicked.connect(self.editTestPlans)
        saveButton = QPushButton("Save")
        saveButton.clicked.connect(self.saveTestPlans)


        ncol = (len(self.incMinute)+2)
        nrow = 4
        mcol = ncol/2
        ecol = len(self.incMinute)+1
        grid.addWidget(QLabel("SolventType"),   0,0,1,mcol)
        grid.addWidget(QLabel("ProteinName"),   1,0,1,mcol)
        #grid.addWidget(QLabel("Replication"),   2,0,1,mcol)
        grid.addWidget(QLabel("DivideSolvent"), 2,0,1,mcol)
        grid.addWidget(QLabel("DivideCryoPro"), 3,0,1,mcol)
        grid.addWidget(self.EvalueSolvType,  0,mcol,1,mcol)
        grid.addWidget(self.EvalueProtein,   1,mcol,1,mcol)
        #grid.addWidget(self.EvalueReplicaNo, 2,mcol,1,mcol)
        grid.addWidget(self.EvalueSolvParts, 2,mcol,1,mcol)
        grid.addWidget(self.EvalueCryoParts, 3,mcol,1,mcol) 
        grid.addWidget(QLabel("Solvent"),     nrow,0)
        #grid.addWidget(QLabel("  0min"),      nrow,1)
        #grid.addWidget(QLabel(" 30min"),      nrow,2)
        #grid.addWidget(QLabel(" 60min"),      nrow,3)
        #grid.addWidget(QLabel("120min"),      nrow,4)
        grid.addWidget(QLabel("CryoPro"),     nrow,ecol)
        for i, itime in enumerate(self.incMinute):
            grid.addWidget(QLabel("{0}h".format(str(itime).rjust(3,' '))),nrow, i+1)
            #grid.addWidget(itime, nrow, i+1)

        #self.solVolumes        = [] #List of QLineEdits for Get Solvent Volume
        #self.crpVolumes        = [] #List of QLineEdits for Get Cryoprotectant Volume
        self.radioButtons = {} #Dcit of RadioButtons for Select A Condition (Solvent Vol / Cryo Vol / Incubation Time)
        
        for cp in self.defaultCP:
            for sv in self.defaultSV:
                if self.EvalueSolvParts.value() != 0 and self.EvalueCryoParts.value() != 0:
                    concs = 'SV{0}nl-CP{1}nl'.format(sv, cp)
                elif self.EvalueSolvParts.value() != 0 and self.EvalueCryoParts.value() == 0:
                    concs = 'SV{0}nl'.format(sv)
                elif self.EvalueSolvParts.value() == 0 and self.EvalueCryoParts.value() != 0:
                    concs = 'CP{0}nl'.format(cp)
                else: pass 
                self.concCondition.append(concs)

        for i in range(len(self.concCondition)):
            sGradient = int(i%(len(self.defaultSV))) 
            self.EvalueSolvVolumes.append(QLineEdit(str(self.defaultSV[sGradient])))
            grid.addWidget(self.EvalueSolvVolumes[i],            i+1+nrow,0)
        k=0
        for i,vol in enumerate(self.defaultCP):
            for sv in self.defaultSV:
                self.EvalueCryoVolumes.append(QLineEdit(str(vol)))
                grid.addWidget(self.EvalueCryoVolumes[k],            k+1+nrow,5)
                k += 1
        
        #for i in range(len(self.incMinute)):
            #for j in range(self.concCondition):
        for i, incu in enumerate(self.incMinute):
            self.EvalueConditions.append([])
            for j, conc in enumerate(self.concCondition):
                buttonKey = '{0},{1}'.format(i,j)
                color = self.colors[i][j]
                self.radioButtons[buttonKey] = QRadioButton() 
                self.radioButtons[buttonKey].setStyleSheet("QRadioButton"
                                                           "{"
                                                          f"background-color:{color}"
                                                           "}")
                # the below: it calls self.pickCondition twice
                self.radioButtons[buttonKey].toggled.connect(self.pickCondition)
                grid.addWidget(self.radioButtons[buttonKey], j+1+nrow,i+1)
                print('[RadioButton]', buttonKey, color)
                #tconc = '{0}min-{1}'.format(incu, conc) 
                tconc = '{0}hr-{1}'.format(incu, conc)
                self.EvalueConditions[i].append(tconc)
                

        grid.addWidget(editButton,  11,0,1,3)
        grid.addWidget(saveButton,  11,3,1,3)
 
        groupbox.setLayout(grid)
        print('me',self.EvalueConditions)

        #self.loadChemImg()
        return groupbox

    def editTestPlans(self):
        if self.EvaluePlanned == True:
            react = QMessageBox.information(self, "Really?", \
                                            "This action could reset you worked on.\nDo you want to continue?", QMessageBox.Cancel, QMessageBox.Yes)
            if react == QMessageBox.Cancel:
                pass
            elif react ==  QMessageBox.Yes:
                self.planType = 'free'
                self.EvaluePlanned = False
                self.EvalueSolvType.setEnabled(True)
                self.EvalueReplicaNo.setReadOnly(False)
                self.EvalueSolvParts.setReadOnly(False)
                self.EvalueCryoParts.setReadOnly(False)
                for SVlineEdit in self.EvalueSolvVolumes:
                    SVlineEdit.setReadOnly(False)
                for CPlineEdit in self.EvalueCryoVolumes:
                    SVlineEdit.setReadOnly(False)
                #self.EvalueExpID = func.createExpID(self.planType, 'new', self.EvalueProtein.text(), self.EvalueExpList)
                self.currentWell  = 1
                self.EvalueTargeting.clear()
                self.moveFirstCondition()
                self.setCurrentWell()
            else: pass

    def saveTestPlans(self):
        check = []
        for i,SVlineEdit in enumerate(self.EvalueSolvVolumes):
            if not (SVlineEdit.text()).isnumeric():
                warning = "Wrong Solvent Volume({0})".format(i+1)
                check.append(warning)
            else: pass
        for i,CPlineEdit in enumerate(self.EvalueCryoVolumes):
            if not (CPlineEdit.text()).isnumeric():
                warning = "Wrong CryoProtectant Volume({0})".format(i+1)
                check.append(warning)
            else: pass
        if len(check) != 0:
            message = '\n'.join(check)
            react = QMessageBox.warning(self, "Check Parameters", message , QMessageBox.Yes)
            if react == QMessageBox.Yes:
                pass
        else:
            if self.EvaluePlanned == False: 
                self.planType = 'pretest'
                self.EvaluePlanned = True
                self.EvalueSolvType.setEnabled(False)
                self.EvalueReplicaNo.setReadOnly(True)
                self.EvalueSolvParts.setReadOnly(True)
                self.EvalueCryoParts.setReadOnly(True)
                for SVlineEdit in self.EvalueSolvVolumes:
                    SVlineEdit.setReadOnly(True)
                for CPlineEdit in self.EvalueCryoVolumes:
                    SVlineEdit.setReadOnly(True)
                self.EvalueExpID = func.createExpID(self.planType, 'new', self.EvalueProtein.text(), self.EvalueExpList)
        self.concCondition = []
        self.EvalueConditions = []
        #for cp in self.defaultCP:

        #for cpLineEdit in self.EvalueCryoVolumes:
        #    cp = cpLineEdit.text()
        #    print('[cryoDebug]', cp)
        #    for svLineEdit in self.EvalueSolvVolumes:
        for i in range(len(self.EvalueSolvVolumes)):
            sv = self.EvalueSolvVolumes[i].text()
            cp = self.EvalueCryoVolumes[i].text()
            if self.EvalueSolvParts.value() != 0 and self.EvalueCryoParts.value() != 0:
                concs = 'SV{0}nl-CP{1}nl'.format(sv, cp)
            elif self.EvalueSolvParts.value() != 0 and self.EvalueCryoParts.value() == 0:
                concs = 'SV{0}nl'.format(sv)
            elif self.EvalueSolvParts.value() == 0 and self.EvalueCryoParts.value() != 0:
                concs = 'CP{0}nl'.format(cp)
            else: pass
            self.concCondition.append(concs)

        for i, incu in enumerate(self.incMinute):
            self.EvalueConditions.append([])
            for j, conc in enumerate(self.concCondition):
                #buttonKey = '{0},{1}'.format(i,j)
                # the below: it calls self.pickCondition twice
                tconc = '{0}min-{1}'.format(incu, conc)
                self.EvalueConditions[i].append(tconc)
        self.EvalueTargeting.clear()
        #move to first condition(radiobutton)
        self.moveFirstCondition()
        
    def drawPlateView(self):
        print('I cannot dra plate view yet')
        imgKey = '{0}_{1}'.format(self.currPlate_view.text(),self.currWell_view.text())
        alpha = imgKey.split('_')[-1][0:1]
        num = imgKey.split('_')[-1][1:3]
        well = alpha+num
        i = ord(alpha) - 65
        j = int(num) - 1
        cnt = 0
        if len(self.EvalueTargeting) != 0:
            for conKey, selected in self.EvalueTargeting.items():
                for iKey, pVal in selected.items():
                    if iKey.startswith(well):
                        cnt += 1
                    else: pass
        else: pass
        self.tableview_model2.setItem(i, j, QStandardItem(str(cnt)))

    def moveFirstCondition(self):
        self.EvalueCurrCondition = self.EvalueConditions[0][0]
        self.radioButtons['0,0'].setChecked(True)


    def moveNextCondition(self):
        print('move to Next condition!')
        print('fullkeys', self.keys)
        i,j = self.getConditionKey()
        currKey = '{0},{1}'.format(i,j)
        currIndex = self.keys.index(currKey)
        nextIndex = currIndex + 1
        try:
            print('move to next key', self.keys[nextIndex])
            self.radioButtons[self.keys[nextIndex]].setChecked(True)
            print(self.EvalueTargeting)
        except IndexError as e:
            print(e)
    #def movePrevCondition(self):

    def getConditionKey(self):
        currentkey = 'none'
        for key, button in self.radioButtons.items():
            if button.isChecked():
                i = int(key.split(',')[0])
                j = int(key.split(',')[1])
               
        return i,j

    def pickCondition(self):
        #currentkey 
        i,j= self.getConditionKey()
        self.EvalueCurrCondition = self.EvalueConditions[i][j]
        print(i,j,self.EvalueCurrCondition)
        self.EvalueTargets[self.EvalueConditions[i][j]] = [] 
        #We will fill out it with points(list in list):
        #  ['Solvent', '00 nl', 'pID', 'Well', 'x offset', 'y offset']
        #  ['CryoPro', '00 nl', 'pID', 'Well', 'x offset', 'y offset']
 
    def loadSelectionMap(self):
        groupbox = QGroupBox()
        grid = QGridLayout()

        self.fillSelectionMap()
        for i in range(0,8,1):
            #platerow = QLabel( str(chr( 64+(8-i) )) )
            platerow = QLabel( str(chr(i+65)) )
            platerow.setAlignment(Qt.AlignCenter)
            grid.addWidget(platerow, 0,i+1)
            #grid.addWidget(platerow.setAlignment(Qt.AlignCenter), 0,i)
        for i in range(1,13,1):
            platecol = QLabel( str(i).zfill(2) )
            platecol.setAlignment(Qt.AlignCenter)
            grid.addWidget(platecol, i, 0) 
        grid.addWidget(self.tableview2, 1, 1,12, 8)
        groupbox.setLayout(grid)
        return groupbox        
      
    def fillSelectionMap(self):
        #tableview.setSortingEnabled(True)
        self.tableview_model2.removeRows(0, self.tableview_model2.rowCount())
        self.tableview_model2.removeColumns(0, self.tableview_model2.columnCount())

        self.tableview2.setModel(self.tableview_model2)
        headers = []
        for i in range(0,8,1):
            #platerow = QLabel( str(chr( 64+(8-i) )) )
            platerow = str(chr(i+65)) 
            headers.append(platerow)
        self.tableview_model2.setHorizontalHeaderLabels(headers)

        for i in range(12): 
            for j in range(8):
                #print(i,j)
                alpha = str(chr(j+65))
                num   = str(i+1).zfill(2)
                well  = alpha+num
                targets   = 0
                condis    = []
                if len(self.EvalueTargeting) != 0:
                    for conKey, selected in self.EvalueTargeting.items():
                        for iKey, pVal in selected.items():
                            iKey_well = iKey.split('_')[-1]
                            if iKey_well.startswith(well):
                                targets += 1
                                print('[drawPlateView]',well,targets)
                                if conKey not in condis:
                                    condis.append(conKey)
                            else: pass
                self.tableview_model2.setItem(i, j, QStandardItem(str(targets)))
                if len(condis) == 0: pass
                elif len(condis) == 1:
                    print(condis, condis[0].split(',')[0])
                    color = self.colors[ int(condis[0].split(',')[0])][int(condis[0].split(',')[-1]) ]
                    self.tableview_model2.setData(self.tableview_model2.index(i,j), QColor(color), Qt.BackgroundRole)
                else: 
                    #black
                    self.tableview_model2.setData(self.tableview_model2.index(i,j), QColor('#000000'), Qt.BackgroundRole)
                    self.tableview_model2.setData(self.tableview_model2.index(i,j), QColor('#FFFFFF'), Qt.ForegroundRole)
                    pass
        self.tableview2.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tableview2.verticalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tableview2.horizontalHeader().setVisible(False)
        self.tableview2.verticalHeader().setVisible(False)
        print(self.tableview_model.rowCount())


    def ScreenPlans(self):
        groupbox = QGroupBox()
        widget = QWidget()
        grid = QGridLayout()

        for item in self.ScreenExpList:
            self.ScreenExpID.addItem(item)

        libList = ['none']
        for item in os.listdir(self.libraryPath):
            print('[Libraries]', item)
            if item.endswith('.csv'):
                #filename = '{0}/{1}'.format(self.libraryPath, item)
                libname  = item.replace('.csv', '')
                libList.append(libname)
            else: pass
        for item in libList:
            print(item, type(item))
            self.SelectLib.addItem(item)
        self.SelectLib.currentTextChanged.connect( self.setCurrLibrary )

        self.SelectLib.setCurrentIndex(0)
        self.ScreenChemVol.setRange(2.5, 300)
        self.ScreenChemVol.setSuffix('nl')
        self.ScreenChemVol.setSingleStep(2.5)
        self.ScreenChemVol.setDecimals(1)
        self.ScreenChemVol.setValue( float(10) )

        self.ScreenChemParts.setRange(0, 10)
        self.ScreenChemParts.setSuffix('point(s)')
        self.ScreenChemParts.setSingleStep(1)
        self.ScreenChemParts.setDecimals(0)
        self.ScreenChemParts.setValue( float(1) )

        #self.ScreenCryoWell.returnPressed.connect( self.estimateCryoWell )
        self.ScreenCryoWell.editingFinished.connect( self.estimateCryoWell )

        self.ScreenCryoVol.setRange(2.5, 500)
        self.ScreenCryoVol.setSuffix('nl')
        self.ScreenCryoVol.setSingleStep(2.5)
        self.ScreenCryoVol.setDecimals(1)
        self.ScreenCryoVol.setValue( float(100) )
        self.ScreenCryoVol.valueChanged.connect( self.zero )

        self.ScreenCryoParts.setRange(0, 10)
        self.ScreenCryoParts.setSuffix('point(s)')
        self.ScreenCryoParts.setSingleStep(1)
        self.ScreenCryoParts.setDecimals(0)
        self.ScreenCryoParts.setValue( float(1) )
        self.ScreenCryoParts.valueChanged.connect( self.zero )



        editButton = QPushButton("Edit")
        #editButton.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_MediaSkipForward')))
        editButton.clicked.connect(self.editScreenPlans)
        saveButton = QPushButton("Save")
        saveButton.clicked.connect(self.saveScreenPlans)

        grid.addWidget(QLabel("  Experiment ID"),      0,0,1,1)
        grid.addWidget(self.ScreenExpID,               0,1,1,3)
        grid.addWidget(QLabel("FragmentLibrary"),      1,0,1,1)
        grid.addWidget(self.SelectLib,                 1,1,1,3)
        grid.addWidget(QLabel("Region"),               2,0,1,1)
        grid.addWidget(self.ScreenChemWell_st,         2,1,1,3)
        grid.addWidget(QLabel("   Protein Name"),      3,0,1,1)
        grid.addWidget(self.ScreenProtein,             3,1,1,3)        
        grid.addWidget(QLabel("    Chemical Vol"),     4,0,1,1)
        grid.addWidget(self.ScreenChemVol,             4,1,1,3)
        grid.addWidget(QLabel("       Divide into"),   5,0,1,1)
        grid.addWidget(self.ScreenChemParts,           5,1,1,3)
        #grid.addWidget(QLabel("     CryoPro pID"),    5,0,1,1)
        #grid.addWidget(self.ScreenCryoPlate,          5,1,1,1)
        grid.addWidget(QLabel("        Cryo well"),    6,0,1,1)
        grid.addWidget(self.ScreenCryoWell,            6,1,1,3)
        grid.addWidget(QLabel("      CryoPro Vol"),    7,0,1,1)
        grid.addWidget(self.ScreenCryoVol,             7,1,1,3)
        grid.addWidget(QLabel("       Divide into"),   8,0,1,1)
        grid.addWidget(self.ScreenCryoParts,           8,1,1,3)
        grid.addWidget(editButton,                     9,0,1,1)
        grid.addWidget(saveButton,                     9,1,1,3)
        #grid.addWidget(self.loadChemical(),        10,0,1,2)
        #grid.setColumnStretch(0,3)
        #grid.setColumnStretch(1,0)
        #grid.setColumnStretch(2,0)



        groupbox.setLayout(grid)


        self.loadChemImg()
        return groupbox


    def saveScreenPlans(self):
        check = []
        if self.SelectLib.currentText() == 'none':
            warning = "No Library Selected"
            check.append(warning)
        if self.ScreenProtein.text().strip() == '': 
            warning = "No Protein Name is given"
            check.append(warning)
        if self.ScreenProtein.text().strip() == 'name':
            warning = "Check the Protein Name"
            check.append(warning)
        if self.ScreenChemVol.value() + self.ScreenCryoVol.value() == 0 or self.ScreenChemParts.value() + self.ScreenCryoParts.value() == 0:
            warning = "No Target Required"
            check.append(warning)
        
        if len(check) != 0:
            message = '\n'.join(check)
            react = QMessageBox.warning(self, "Check Parameters", message , QMessageBox.Yes)
            if react == QMessageBox.Yes:
                pass
        else:
            if self.ScreenPlanned == False:
                self.planType = 'screen'
                self.ScreenPlanned = True
                self.ScreenExpID.setEnabled(False)
                self.ScreenProtein.setReadOnly(True)
                self.SelectLib.setEnabled(False)
                self.ScreenChemVol.setReadOnly(True)
                self.ScreenChemParts.setReadOnly(True)
                self.ScreenCryoPlate.setEnabled(False)
                self.ScreenCryoWell.setEnabled(False)
                self.ScreenCryoVol.setReadOnly(True)
                self.ScreenCryoParts.setReadOnly(True)
                #self.ScreenCryoParts.setStyleSheet('QComboBox{background:#efefef}')

                self.Targeting.clear()
                #20220929 RM self.ScreenChemWell = 1
                #20220929 RM self.currentWell  = 1
                #20220929 RM self.ScreenChemWell_fin = 1
                self.ScreenChemWell = int(self.ScreenChemWell_st.value())
                self.ScreenChemWell_fin = int(self.ScreenChemWell_st.value())
                self.currentWell = int(self.ScreenChemWell_st.value())
                self.ScreenCryoCnt = 1
                self.setCurrLibrary()            
                self.setCurrentWell()
                chemEachVol = misc.calEachSites( float(self.ScreenChemVol.value()), int(self.ScreenChemParts.value()))
                cryoEachVol = misc.calEachSites( float(self.ScreenCryoVol.value()), int(self.ScreenCryoParts.value()))
                self.EachVol = chemEachVol + cryoEachVol
                if self.ScreenExpID.currentText() == 'New' or self.ScreenExpID.currentText() == 'new':
                    self.NewExpID = func.createExpID(self.planType, self.ScreenExpID.currentText(), self.ScreenProtein.text(), self.ScreenExpList)
                    print('[ExperimentID] ', self.NewExpID)
                    self.ScreenExpList.insert(1, self.NewExpID)
                    self.ScreenExpID.clear()
                    for item in self.ScreenExpList:
                        #print(item)
                        self.ScreenExpID.addItem(item)
                    self.ScreenExpID.setCurrentText(self.ScreenExpList[1])
            else:
                pass

    def editScreenPlans(self):
        if self.ScreenPlanned == True:
            react = QMessageBox.information(self, "Really?", \
                                            "This action could reset you worked on.\nDo you want to continue?", QMessageBox.Cancel, QMessageBox.Yes)
            if react == QMessageBox.Cancel:
                pass
            elif react ==  QMessageBox.Yes:
                print('cleaning')
                self.Targeting.clear()
                self.ScreenChemWell = 1
                self.ScreenChemWell_fin = 1
                self.ScreenCryoCnt = 1
                self.currentWell  = 1
                self.setCurrLibrary()
                self.setCurrentWell()
                self.ScreenPlanned = False
                self.ScreenExpID.setEnabled(True)
                self.ScreenProtein.setReadOnly(False)
                self.SelectLib.setEnabled(True)
                self.ScreenChemVol.setReadOnly(False)
                self.ScreenChemParts.setReadOnly(False)
                self.ScreenCryoPlate.setEnabled(True)
                self.ScreenCryoWell.setEnabled(True)
                self.ScreenCryoVol.setReadOnly(False)
                self.ScreenCryoParts.setReadOnly(False)
            else: pass
        else:
            pass


    def noneChemical(self):
        self.view_chemLib    .setText( str('none') )
        self.view_chemWell   .setText( str('***') )
        self.view_chemID     .setText( str( '') )
        self.view_chemFormula.setText( str(' ') )
        self.view_chemSMILE  .setText( str(':)') )
        self.view_chemConc   .setText( str(' ') )
        self.view_chemMW     .setText( str(' ') )
        self.view_chemSolvent.setText( str(' ') )
        self.view_chemVendor .setText( str(' ') )


    def loadChemical(self):
        infoWidget = QWidget()
        infogrid = QGridLayout()
        groupbox = QGroupBox()
        grid = QGridLayout()

        infogrid.addWidget(QLabel("Current Library"),     0,0)
        infogrid.addWidget(self.view_chemLib,             0,1)
        infogrid.addWidget(QLabel("Current Well"),        1,0)
        infogrid.addWidget(self.view_chemWell,            1,1)
        infogrid.addWidget(QLabel("Chemical ID"),         2,0)
        infogrid.addWidget(self.view_chemID,              2,1)
        infogrid.addWidget(QLabel("Formular"),            3,0)
        infogrid.addWidget(self.view_chemFormula,         3,1)
        infogrid.addWidget(QLabel("SMILE"),               4,0)
        infogrid.addWidget(self.view_chemSMILE,           4,1)
        infogrid.addWidget(QLabel("Concentration"),       5,0)
        infogrid.addWidget(self.view_chemConc,            5,1)
        infogrid.addWidget(QLabel("Mol Weight"),          6,0)
        infogrid.addWidget(self.view_chemMW,              6,1)
        infogrid.addWidget(QLabel("Solvent"),             7,0)
        infogrid.addWidget(self.view_chemSolvent,         7,1)
        infogrid.addWidget(QLabel("Vendor"),              8,0)
        infogrid.addWidget(self.view_chemVendor,          8,1)

        infoWidget.setLayout(infogrid)

        grid.addWidget(self.CimgLabel, 0,0)        
        grid.addWidget(infoWidget,     1,0)
        self.CimgLabel.setAlignment(Qt.AlignCenter | Qt.AlignCenter)

        groupbox.setLayout(grid)

        return groupbox

    def loadChemInfo(self):
        currentLibrary = self.SelectLib.currentText()
        currLibPath = '{0}/{1}.csv'.format(self.libraryPath, currentLibrary)
        try:
            if currentLibrary != 'none' and os.path.isfile(currLibPath):
                #self.ScreenChemLib_info, LibPlates = misc.libraryToDict(currLibPath)
                #for i, item in enumerate(self.ScreenChemLib_info):
                    #print(i,item)
                libraryname = self.ScreenChemLib_info[self.ScreenChemWell]['Library']
                chemid      = self.ScreenChemLib_info[self.ScreenChemWell]['ChemID']
                chemsmile   = self.ScreenChemLib_info[self.ScreenChemWell]['SMILE']
                chemaddress = self.ScreenChemLib_info[self.ScreenChemWell]['PlateID']
                #print('change', self.ScreenChemWell, self.ScreenChemLib_info[self.ScreenChemWell])
                print("[CurrentChem] {0}@{1}-{2}".format(chemid, libraryname, chemaddress))
                self.view_chemLib    .setText(str(currentLibrary))
                self.view_chemWell   .setText(str(self.ScreenChemLib_info[self.ScreenChemWell]['PlateWell']))             
                self.view_chemID     .setText(str(self.ScreenChemLib_info[self.ScreenChemWell]['ChemID']))
                self.view_chemFormula.setText(str(self.ScreenChemLib_info[self.ScreenChemWell]['Formula']))
                self.view_chemSMILE  .setText(str(self.ScreenChemLib_info[self.ScreenChemWell]['SMILE']))
                self.view_chemConc   .setText(str('{0} mM'.format( self.ScreenChemLib_info[self.ScreenChemWell]['Conc'] )))
                self.view_chemMW     .setText(str('{0} g/mole'.format( self.ScreenChemLib_info[self.ScreenChemWell]['MW'] )))
                self.view_chemSolvent.setText(str(self.ScreenChemLib_info[self.ScreenChemWell]['Solvent']))
                self.view_chemVendor .setText(str(self.ScreenChemLib_info[self.ScreenChemWell]['Vendor']))
            #print('cchange', self.chemLib, self.ScreenChemWell)

            else:
                self.noneChemical()
        except IndexError as e:
            print(e)
            print('End of Library')

    def loadChemImg(self):
        try:
            if self.SelectLib.currentText() == 'none':
                imgFilePath = '/usr/local/XtalViewer/1.0/img/testtube.png'
            else:
                libname   = self.SelectLib.currentText().split(' ')[0].strip()
                libheader = ['ChemID', 'Vendor', 'Library', 'PlateID', 'PlateWell', 'Formular', 'MW', 'Conc', 'Solvent', 'SMILE']
                chemName  = self.ScreenChemLib_info[self.ScreenChemWell]['ChemID']
                chemSmile = self.ScreenChemLib_info[self.ScreenChemWell]['SMILE']
                imgPath = '{0}/{1}/{2}'.format(self.chemsPath, libname, chemName)
                imgFilePath = '{0}/{1}.png'.format(imgPath, chemName)
                if not os.path.isfile(imgPath):
                    chem.drawFormular(chemName, chemSmile, imgPath)
                else:
                    pass
            image = QImage(imgFilePath)
            if image.isNull():
                QMessageBox.information(self, "Image Viewer", "Cannot load %s." % imgFilePath)
                return
            pixmap = QPixmap(image)
            #pixmap = pixmap.scaledToWidth(s)
            pixmap = pixmap.scaledToHeight(200)
            self.CimgLabel.setPixmap(pixmap)
      
        except IndexError as e: pass
            
    def setCurrLibrary(self):
        expid = self.ScreenExpID.currentText()
        currentLibrary = self.SelectLib.currentText()
        currLibPath = '{0}/{1}.csv'.format(self.libraryPath, currentLibrary)
        if currentLibrary != 'none' and os.path.isfile(currLibPath):
            if expid in self.expOnWeb:
                doneList = getMxlive.successXtals(expid)[::-1]
                print(doneList)
                self.ScreenChemLib_info, self.ScreenChemPlates = misc.spareLibToDict(currLibPath, doneList)
            else:
                self.ScreenChemLib_info, self.ScreenChemPlates = misc.libraryToDict(currLibPath)
                print('library size :',len(self.ScreenChemLib_info))
                print(self.ScreenChemLib_info)
            self.ScreenChemWell_st.setRange(1,len(self.ScreenChemLib_info))
        self.loadChemInfo()
        self.loadChemImg()
        #self.estimateCryoWell()
        self.ScreenChemWell = int(self.ScreenChemWell_st.value())

    def setCurrChemWell(self):        
        self.loadChemInfo()
        self.loadChemImg()

    def moveChemWell(self):
        if self.SelectLib.currentText() != 'none':
            imgKey = '{0}_{1}'.format(self.currPlate_view.text(),self.currWell_view.text())
            print(self.Targeting)
            if imgKey in self.Targeting:
                for item in self.Targeting[imgKey]:
                    print('item', item)
                    prefix   = item[0].split(' ')[0].strip() 
                    #no       = point[0].split(' ')[1].strip()
                    #chemPID  = point[0].split(' ')[2].strip()
                    #chemWell = point[0].split(' ')[3].strip()
                    if prefix == 'chem' or prefix == 'Chem' or prefix == 'CHEM':
                        no       = item[0].split(' ')[1].strip() #number in librar
                        self.ScreenChemWell = int(no)
                        self.setCurrChemWell()
                        print("[CurrentWell] {0} already has chem Target".format(imgKey))
                    else: pass
                        #print("[{0}] No chem Target in this well".format(imgKey))
            else:
                self.ScreenChemWell = len(self.Targeting)+ 1
            self.setCurrChemWell()

        else: pass

    def moveNextChemWell(self):
        #time.sleep(3)
        if self.SelectLib.currentText() != 'none':
            if self.ScreenChemWell == len( self.ScreenChemLib_info ) - 1:
                print('[Note] Last Chem in this Library')
            else:
                imgKey = '{0}_{1}'.format(self.currPlate_view.text(),self.currWell_view.text())
                if imgKey in self.Targeting:
                    self.ScreenChemWell += 1
                    self.setCurrChemWell()
                else:
                    pass
        else: pass

    def moveBothWell(self):
        pID = self.currPlate_view.text()
        well = self.currWell_view.text()
        imgKIey = '{0}_{1}'.format(pID,well)
        #if imgKey in self.Targeting:
            

    def estimateCryoWell(self):
        if self.SelectLib.currentText() != 'none':
            libraryScale = len(self.ScreenChemLib_info)-1
            cryoVol = self.ScreenCryoVol.value()
            userWells = func.validateWell(self.ScreenCryoWell.text())
            prepWells = func.wellsToPrep(libraryScale, self.ScreenCryoWellVol, cryoVol, userWells)
            self.ScreenCryoWell.setText(', '.join(prepWells))
            """
            grid384 = []
            for i in range(1,25):
                for j in range(65, 81):
                    w = '{0}{1}'.format(chr(j),str(i).zfill(2))
                    grid384.append(w)
            libraryScale = len(self.ScreenChemLib_info)-1
            validVol = 2800
            chambers = math.ceil( float(self.ScreenCryoVol.value()) * libraryScale / validVol )
            times = math.trunc( validVol / float( self.ScreenCryoVol.value() ) )
            userWells = func.validateWell(self.ScreenCryoWell.text())
            if len(userWells) == 0:
                print('[ Warning ] Enter the first well to prepare CryoProtectant')
            else:
                fromWhere = grid384.index(userWells[-1])
                if len(grid384[fromWhere:]) < chambers:
                    print('[ Warning ] Prepare Another plate for CryoProtectant') 
                else:
                    if len(userWells) >= chambers:
                        pass
                    else:
                        fromWhere = grid384.index(userWells[-1])
                        while len(userWells) < chambers:
                            #nextWell = chr( ord( userWells[-1][0] ) + 1 ) + userWells[-1][1:]
                            fromWhere += 1 
                            nextWell = grid384[fromWhere]
                            userWells.append(nextWell)
                        self.ScreenCryoWell.setText(', '.join(userWells))
                        print('needed wells:', chambers, 'userWell++', userWells)               
            """
        else: pass
        



    def zero(self):
        print("I'm not anything")

    def imageClickEvent(self, event):
        #text = "LeftClick: x={0}, y={1}, global = {2},{3}".format(event.x()-zero[0], event.y()-zero[1], event.globalX(), event.globalY())

        try:
            rowXoffset = int( self.currWell_view.text()[1:3] ) * self.colOffset[0] - self.colOffset[0]
            rowYoffset = int( self.currWell_view.text()[1:3] ) * self.colOffset[1] - self.colOffset[1]
            colXoffset = ( ord(self.currWell_view.text()[0]) - 65 ) * self.rowOffset[0]
            colYoffset = ( ord(self.currWell_view.text()[0]) - 65 ) * self.rowOffset[1]
            Xoffset = rowXoffset + colXoffset
            Yoffset = rowYoffset + colYoffset

            #zero = [ (self.guideShift1[0]+self.guideRadius) * self.imgScale.value()/100,\
            #         (self.guideShift1[1]+self.guideRadius) * self.imgScale.value()/100 ] 
            zero = [ (self.guideShift1[0]+self.guideRadius+Xoffset) * self.imgScale.value()/100,\
                     (self.guideShift1[1]+self.guideRadius+Yoffset) * self.imgScale.value()/100 ] 
 
            xPixcelPosition = (event.x()-zero[0]) / (self.imgScale.value()/100)
            yPixcelPosition = (event.y()-zero[1]) / (self.imgScale.value()/100)
            xMeterPosition = (event.x()-zero[0]) * self.wellRadius / (self.guideRadius * self.imgScale.value() /100)
            yMeterPosition = (event.y()-zero[1]) * self.wellRadius / (self.guideRadius * self.imgScale.value() /100)


            pID = self.currPlate_view.text()
            well = self.currWell_view.text()
            imgKey = '{0}_{1}'.format(pID,well)
            if event.buttons() & Qt.LeftButton:
                #text = "LeftClick: x={0}px, y={1}px in {2}_{3}".format(xPixcelPosition, yPixcelPosition, pID, well)
                #text = "LeftClick: x={0}um, y={1}um in {2}_{3}".format(round(xMeterPosition,0), round(yMeterPosition,0), pID, well)
                if len(self.scale[1]) == 2:
                    self.scale[1] = []
                    self.scale[1].append([round(xMeterPosition,0),round(yMeterPosition,0), xPixcelPosition, yPixcelPosition])
                    text = "LeftClick: x={0}um, y={1}um in {2}_{3}".format(round(xMeterPosition,0), round(yMeterPosition,0), pID, well)
                elif len(self.scale[1]) == 1:
                    self.scale[1].append([round(xMeterPosition,0),round(yMeterPosition,0), xPixcelPosition, yPixcelPosition])
                    x1 = self.scale[1][0][0]
                    y1 = self.scale[1][0][1]
                    x2 = self.scale[1][1][0]
                    y2 = self.scale[1][1][1]
                    distance = (abs(x1-x2)**2 + abs(y1-y2)**2)**(1/2)
                    text = "LeftClick: x={0}um, y={1}um in {2}_{3} // Distance: {4}um".format(round(xMeterPosition,0), round(yMeterPosition,0), pID, well, round(distance,0))
                else: 
                    self.scale[1].append([round(xMeterPosition,0),round(yMeterPosition,0), xPixcelPosition, yPixcelPosition])
                    text = "LeftClick: x={0}um, y={1}um in {2}_{3}".format(round(xMeterPosition,0), round(yMeterPosition,0), pID, well)
                print("LeftClick: x={0}um, y={1}um [ x={2}px, y={3}]".format(round(xMeterPosition,0), round(yMeterPosition,0), xPixcelPosition, yPixcelPosition))
                self.window.statusbar.showMessage(text)
            if event.button() & Qt.RightButton:
                #if self.SelectLib.currentText() == 'none':
                if self.planType == 'free': 
                    text = "RightClick: x={0}um, y={1}um in {2}_{3}".format(round(xMeterPosition,0), round(yMeterPosition,0), pID, well)
                    points = ['free', round(xMeterPosition,0),round(yMeterPosition,0), xPixcelPosition, yPixcelPosition]
                    self.window.statusbar.showMessage(text)
                    try:
                        if len(self.Targeting[imgKey]) < self.targetSite.value():
                            self.Targeting[imgKey].append( points )
                        else: pass
                    except KeyError as err:
                        self.Targeting[imgKey] = [ points ]
                    finally:
                        self.loadImage()
                        targetsInThisWell = len( self.Targeting[imgKey] )
                        if targetsInThisWell == self.targetSite.value():
                            print(targetsInThisWell ,self.targetSite.value())
                            self.moveNextWell()
                        else: pass
                elif self.planType == 'pretest':
                    text = "RightClick: PretestTarget> x={0}um, y={1}um in {2}_{3}".format(round(xMeterPosition,0), round(yMeterPosition,0), pID, well)
                    self.window.statusbar.showMessage(text)
                    print(self.EvaluePlanned)
                    if self.EvaluePlanned == True:
                        ##write a description for point
                        ##it can be added to target list or not (decided by other part) 
     
                        i,j = self.getConditionKey()
                        conKey = '{0},{1}'.format(i,j)
                        #if self.EvalueSolvParts.value() != 0:
                        #    solv_points = ['solvent', round(xMeterPosition,0),round(yMeterPosition,0), xPixcelPosition, yPixcelPosition]
                        #else: pass
                        #if self.EvalueCryoParts.value() != 0:
                        #    cryo_points = ['cryopro', round(xMeterPosition,0),round(yMeterPosition,0), xPixcelPosition, yPixcelPosition]
                        #else: pass
                        solv_points = ['solvent', round(xMeterPosition,0),round(yMeterPosition,0), xPixcelPosition, yPixcelPosition]
                        cryo_points = ['cryopro', round(xMeterPosition,0),round(yMeterPosition,0), xPixcelPosition, yPixcelPosition]

                        ## Now, make a decision to use this point or not
                        if self.EvalueSolvParts.value() == 0 and self.EvalueCryoParts.value() == 0:
                            print('[ Warning ] No Targets to select')
                        elif self.EvalueSolvParts.value() != 0 and self.EvalueCryoParts.value() == 0:
                            if conKey not in self.EvalueTargeting:
                                self.EvalueTargeting[conKey] = {imgKey:[solv_points]}
                            elif len(self.EvalueTargeting[conKey]) == self.EvalueReplicaNo.value():
                                pass
                            else:
                                if imgKey not in self.EvalueTargeting[conKey] and len(self.EvalueTargeting[conKey]) < self.EvalueReplicaNo.value():
                                    self.EvalueTargeting[conKey][imgKey] = [solv_points]
                                elif len(self.EvalueTargeting[conKey]) <= self.EvalueReplicaNo.value() and len(self.EvalueTargeting[conKey][imgKey]) < self.EvalueSolvParts.value():
                                    self.EvalueTargeting[conKey][imgKey].append( solv_points )
                                else: pass
                            self.loadImage()
                        elif self.EvalueSolvParts.value() == 0 and self.EvalueCryoParts.value() != 0:
                            if conKey not in self.EvalueTargeting:
                                self.EvalueTargeting[conKey] = {imgKey:[cryo_points]}
                            elif len(self.EvalueTargeting[conKey]) == self.EvalueReplicaNo.value():
                                pass
                            else: 
                                if imgKey not in self.EvalueTargeting[conKey] and len(self.EvalueTargeting[conKey]) < self.EvalueReplicaNo.value():
                                    self.EvalueTargeting[conKey][imgKey] = [cryo_points]
                                elif len(self.EvalueTargeting[conKey]) <= self.EvalueReplicaNo.value() and len(self.EvalueTargeting[conKey][imgKey]) < self.EvalueCryoParts.value():
                                    self.EvalueTargeting[conKey][imgKey].append( cryo_points )
                                else: pass
                            self.loadImage()
                        else:
                            if conKey not in self.EvalueTargeting:
                                #self.EvalueTargeting[conKey]         : Selected Wells in This Condition
                                #self.EvalueTargeting[conKey][imgKey] : Points in this well
                                self.EvalueTargeting[conKey] = {imgKey:[solv_points]}
                            elif len(self.EvalueTargeting[conKey]) == self.EvalueReplicaNo.value():
                                try:
                                    #Is there all spots?
                                    solvCnt = 0
                                    cryoCnt = 0
                                    for spot in self.EvalueTargeting[conKey][imgKey]:
                                        prefix = spot[0]
                                        if prefix == 'solvent': solvCnt += 1
                                        elif prefix == 'cryopro': cryoCnt += 1
                                        else: pass
                                    if solvCnt < self.EvalueSolvParts.value():
                                        self.EvalueTargeting[conKey][imgKey].append( solv_points )
                                    elif cryoCnt < self.EvalueCryoParts.value():
                                        self.EvalueTargeting[conKey][imgKey].append( cryo_points )
                                    elif solvCnt + cryoCnt == self.EvalueSolvParts.value() + self.EvalueCryoParts.value():
                                        pass
                                    else: pass
                                except KeyError as e:
                                    print(e)
                                    print('condition[{0}] got all targets'.format(conKey, self.EvalueTargeting[conKey]))
                            else:
                                if imgKey not in self.EvalueTargeting[conKey] and len(self.EvalueTargeting[conKey]) < self.EvalueReplicaNo.value():
                                    self.EvalueTargeting[conKey][imgKey] = [solv_points]
                                elif len(self.EvalueTargeting[conKey]) <= self.EvalueReplicaNo.value() and len(self.EvalueTargeting[conKey][imgKey]) < self.EvalueSolvParts.value():
                                    self.EvalueTargeting[conKey][imgKey].append( solv_points )
                                elif len(self.EvalueTargeting[conKey]) <= self.EvalueReplicaNo.value() and len(self.EvalueTargeting[conKey][imgKey]) == self.EvalueSolvParts.value():
                                    self.EvalueTargeting[conKey][imgKey].append( cryo_points )
                                elif len(self.EvalueTargeting[conKey]) <= self.EvalueReplicaNo.value() and len(self.EvalueTargeting[conKey][imgKey]) < self.EvalueSolvParts.value() + self.EvalueCryoParts.value():
                                    self.EvalueTargeting[conKey][imgKey].append( cryo_points )
                                else: pass
                            self.loadImage()
                        #self.drawPlateView()
                        self.fillSelectionMap()
                        self.doneMove()
                    else: pass





                elif self.planType == 'screen':
                    text = "RightClick: x={0}um, y={1}um in {2}_{3}".format(round(xMeterPosition,0), round(yMeterPosition,0), pID, well)
                    self.window.statusbar.showMessage(text)
                    if  self.ScreenPlanned == True:
                        if self.ScreenChemParts.value() != 0:
                            chemPID = self.ScreenChemLib_info[self.ScreenChemWell]['PlateID'].strip()
                            chemWell = self.ScreenChemLib_info[self.ScreenChemWell]['PlateWell'].strip()
                            chem = 'chem {0} {1} {2}'.format(self.ScreenChemWell, chemPID, chemWell)
                            chem_points = [chem, round(xMeterPosition,0),round(yMeterPosition,0), xPixcelPosition, yPixcelPosition]
                        else: pass
                        if self.ScreenCryoParts.value() != 0:
                            cryoPID = self.ScreenCryoPlate.text().strip()
                            cryoWells = self.ScreenCryoWell.text().split(',')
                            cryoWell  = func.whichCryoWell(cryoWells, self.ScreenCryoWellVol, self.ScreenCryoVol.value(), self.ScreenCryoParts.value(), self.ScreenCryoCnt)
                            cryo = 'cryo {0} {1} {2}'.format(self.ScreenCryoCnt, cryoPID, cryoWell)
                            cryo_points = [cryo, round(xMeterPosition,0),round(yMeterPosition,0), xPixcelPosition, yPixcelPosition]
                        else: pass

                        if self.ScreenChemParts.value() == 0 and self.ScreenCryoParts.value() == 0:
                            print('[ Warning ] No  targets to select')
                        elif self.ScreenChemParts.value() != 0 and self.ScreenCryoParts.value() == 0:
                            if imgKey not in self.Targeting:
                                self.Targeting[imgKey] = [ chem_points ]
                            else:
                                self.Targeting[imgKey].append( chem_points )
                            self.loadImage()

                        elif self.ScreenChemParts.value() == 0 and self.ScreenCryoParts.value() != 0:
                            if imgKey not in self.Targeting:
                                self.Targeting[imgKey] = [ cryo_points ]
                                self.ScreenCryoCnt += 1
                            else:
                                self.Targeting[imgKey].append( cryo_points )
                                self.ScreenCryoCnt += 1
                            self.loadImage()
                       
                        else:
                            if imgKey not in self.Targeting:
                                self.Targeting[imgKey] = [ chem_points ]
                                #self.loadImage()
                            elif len(self.Targeting[imgKey]) < self.ScreenChemParts.value():
                                self.Targeting[imgKey].append( chem_points )
                                #self.loadImage()
                            elif len(self.Targeting[imgKey]) == self.ScreenChemParts.value():
                                self.Targeting[imgKey].append( cryo_points )
                                #self.loadImage()
                                self.ScreenCryoCnt += 1
                            elif len(self.Targeting[imgKey]) < self.ScreenChemParts.value()+self.ScreenCryoParts.value():
                                self.Targeting[imgKey].append( cryo_points )
                                #self.loadImage()
                                self.ScreenCryoCnt += 1
                            else: pass
                            self.loadImage()
                        self.doneMove()
                    else:
                        print('[ Warning ] Check Parameters and Save them')
            #print(self.Targeting)
        except ValueError as e: pass
        except IndexError as e: pass


    def imageResizeEvent(self, event):
        print( 'resize event!', event.oldSize(), event.size() )
        #print('[WindScale] Current Size: {0} x {1}'.format(  event.size().width(), event.size().height() ))
        #if event.size().height() < 709:
        #    self.imgScale.setValue(30)
        #elif event.size().height() >= 709 and event.size().height() < 760:
        #    self.imgScale.setValue(35)
        #elif event.size().height() >= 760 and event.size().height() < 863:
        #    self.imgScale.setValue(40)
        #else:pass 
            #self.imgScale.setValue(50)
        try:
            self.loadImage()
        except IndexError:
            print("No Image yet")


if __name__ == '__main__':
    import sys
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)
    #ex = MyApp()
    ex2 = index()
    sys.exit(app.exec_())
