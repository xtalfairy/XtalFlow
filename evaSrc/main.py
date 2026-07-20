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

        self.ImageType    = QComboBox(self)
        self.PlateType    = QComboBox(self)
        self.targetSite = QSpinBox()
        self.targetVol  = QLineEdit('0')
        self.xCenter  = QSlider(Qt.Horizontal)
        self.yCenter  = QSlider(Qt.Horizontal)
        self.imgScale = QDoubleSpinBox()

        self.objectPlates = QLineEdit()
        self.tableview = QTableView()
        self.tableview_model = QStandardItemModel()
        self.tableview.setSortingEnabled(True)
        self.currPlate_view = QLineEdit()
        self.currWell_view  = QLineEdit()
        self.currSelect_view = QLabel('0')
        #General Definitions
        self.iconColor = '333333'
        self.accpath = {'icons'   :'/usr/local/XtalViewer/1.0/img/icons'}
        self.pathway = {'cellar'  :'/data/users/{0}/FBDD'.format(misc.getUsername()),\
                        'rmserver':'/smbmount/rmserver/RockMakerStorage/WellImages',\
                        'echo650' :'/smbmount/echo650',\
                        'shifter1':'/smbmount/shifter1',\
                        'shifter2':'/smbmount/shifter2'}
        #self.cellarPath   = "/usr/local/XtalViewer/1.0/data"
        self.cellarPath   = "/data/users/{0}/FBDD/record".format(misc.getUsername())
        self.ImageTypeDic = {'Visible':'1', 'Contrast':'3?', 'HighRes':'5', 'Polarize':'8'}
        self.ImageProfile = "profileID_1"
        self.plateGrid = {"row" : ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'], \
                          "col" : [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], \
                          "baby": ['a','c','d']} 
        #self.wellRadius = 1.4mm (1400um)
        self.wellRadius  = 1400
        self.guideRadius = 485
        self.guideShift0 = [150, 50]
        self.guideShift1 = [150, 50]
        self.colOffset = [5.2, -1.7]
        self.rowOffset = [2.2, 3.7]

        #self.screenConfigPath = '/usr/local/XtalViewer/1.0/src/config/screen'
        self.ConfigPath = "/data/users/{0}/FBDD/config".format(misc.getUsername())
        self.screenConfig0 = {'xShift':0, 'yShift':0, 'imgScale':40}
        self.screenConfig1 = {}
        self.OrgImage = [0,0]
        self.plateCatalogue = {}
        self.zeroPlate = '690'
        self.currentPlate = 'none'
        self.currPlate_info = []
        self.currentWell  = 1
        #self.currentState = {'plate':self.zeroPlate, 'well':'***'}
        self.Targeting = {}
        self.checkedPlates = []
        self.scale = [False, []]

        self.temp = [0,0]
        #drawGraph
        self.fig = plt.Figure()
        self.canvas = FigureCanvas(self.fig)        

        #Chemical Library
        self.libraryPath = '/usr/local/XtalViewer/1.0/chems'
        self.chemsPath = "{0}/chems".format(self.pathway['cellar'])
        self.proteinName = QLineEdit('name')
        self.planConfirmed = False
        self.expOnWeb = getMxlive.expOnMxlive()[::-1]
        self.expList = ['New'] + self.expOnWeb
        self.ExperimentID = QComboBox(self)
        self.selectLib   = QComboBox(self)
        self.chemVol  = QDoubleSpinBox()
        self.chemSite = QDoubleSpinBox()
        self.chemLib_info  = []
        self.chemPlates = []
        self.chemWell = 1
        self.chemWell_fin = 1
        self.cryoPlate = QLineEdit('Labcyte00')
        self.cryoWell  = QLineEdit('A1')
        self.cryoWells = []
        self.cryoWellVol = 28000
        self.cryoVol   = QDoubleSpinBox()
        self.cryoSite  = QDoubleSpinBox()
        self.cryoVol   = QDoubleSpinBox()
        self.cryoCnt   = 1 

        self.NewExpID = 'none'
        self.xtalsToHarvest = []
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
        grid.addWidget(QLabel('Targets/Well'),   2,0)
        grid.addWidget(self.targetSite,          2,1)
        grid.addWidget(QLabel('Target Vol'),     3,0)
        grid.addWidget(self.targetVol,           3,1)

        groupbox.setMaximumHeight(200)
        groupbox.setMaximumWidth(250)
        groupbox.setLayout(grid)
        return groupbox

    def selectImageType(self):
        self.ImageProfile = "profileID_{0}".format(self.ImageTypeDic[self.ImageType.currentText()])
        print(self.ImageProfile)

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
        print('centerX', self.guideShift1[0])
        print(self.xCenter.value())
        center = self.guideShift0[0] + self.xCenter.value()
        self.guideShift1[0] = center
        print('centerX', self.guideShift1[0])
        try:
            self.loadImage()
        except IndexError as e:
            pass
    def moveCenterY(self):
        center = self.guideShift0[1] + self.yCenter.value()
        self.guideShift1[1] = center
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
        self.window.resize(self.temp[0],self.tem[1])

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
        try:
            self.currWell_view.setText(self.currPlate_info[self.currentWell][2])
            self.loadImage()
        except IndexError: pass 

        targetsInThisPlate = len( [ key for key,val in self.Targeting.items() if key.startswith(self.currentPlate) ] )
        self.currSelect_view.setText(str(targetsInThisPlate))

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
        else: pass
            
    def moveNextWell(self):
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

    def moveFirstWell(self):
        if self.currentPlate != 'none':
            if self.currentWell == 1:
                pass
            else: 
                self.currentWell = 1
                self.setCurrentWell()
        else: pass
    def moveLastWell(self):
        if self.currentPlate != 'none':
            if self.currentWell == len( self.currPlate_info ) - 1:
                pass
            else:
                self.currentWell = len( self.currPlate_info ) - 1
                self.setCurrentWell()
        else: pass

    def removeLastTarget(self):
        #self.Targeting = {'318_A02d': [[-807.0, -419.0, -111.80000000000001, -58.0], [-475.0, 325.0, -65.80000000000001, 45.0] ...]}
        print(self.Targeting)
        if self.currentPlate != 'none':
            try:
                imgKey  = '{0}_{1}'.format(self.currPlate_info[self.currentWell][1], self.currPlate_info[self.currentWell][2])
                del self.Targeting[imgKey][-1]
                self.Targeting = { k:v for k,v in self.Targeting.items() if len(v) != 0 }
                self.setCurrentWell()
            except KeyError as e:
                print('[ Warning ] No Target in this well')
        else: pass
        print(self.Targeting)

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
        print(self.currPlate_info[self.currentWell])
        if imgPath != 'yet':
            print('imgPath: {}'.format(imgPath))
            image = QImage(imgPath)
            if image.isNull():
                QMessageBox.information(self, "Image Viewer", "Cannot load %s." % imgPath)
                return
            self.pathLineEdit.setText(imgPath)

            self.OrgImage[0] = image.width()
            self.OrgImage[1] = image.height()
            print('[Image Load] Original Image : {0} x {1}'.format( image.width(), image.height() ))

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
                for points in self.Targeting[imgKey]:
                    #orgX = center[0]+points[3]/(self.imgScale.value()/100)
                    #orgY = center[1]+points[4]/(self.imgScale.value()/100)
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
            except:
                pass
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
        if imgKey not in self.Targeting: pass
        else:
            print(self.Targeting)
            print(self.chemSite.value() + self.cryoSite.value())
            if len(self.Targeting[imgKey]) == self.chemSite.value() + self.cryoSite.value():
                for i, item in enumerate(self.Targeting[imgKey]):
                    if len(item[0].split(' ')) == 5: pass
                    else:
                        temp = item[0]+' '+str(self.EachVol[i])
                        item[0] = temp
                if self.chemSite.value() != 0:
                    self.Targeting[imgKey].append( [ 'ps', self.chemLib_info[self.chemWell]['PlateID'],\
                                                           self.chemLib_info[self.chemWell]['PlateWell'],\
                                                           self.chemLib_info[self.chemWell]['ChemID'],\
                                                           self.chemLib_info[self.chemWell]['SMILE'],\
                                                           self.currPlate_info[self.currentWell][-1] ] )
                else: pass
                self.moveNextWell()
                self.moveNextChemWell()
            else: pass



    def chemNavigator(self):
        groupbox = QGroupBox(" ")
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
        #                                                           self.ExperimentID.currentText(),\
        #                                                           self.expList,\
        #                                                           self.proteinName.text(),\
        #                                                           self.Targeting, self.pathway, self.xtalsToHarvest))
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

        grid.addWidget(self.chemPlans(),        0,0)
        grid.addWidget(self.loadChemical(),     1,0)
        grid.addWidget(buttonWidget,            2,0)
        groupbox.setMaximumWidth(250)
        groupbox.setMinimumHeight(300)
        groupbox.setLayout(grid)

        return groupbox

        

    def workSheets(self):
        jsonpath, self.xtalsToHarvest = func.targetsToCSV(self.PlateType.currentText(),\
                                               self.ExperimentID.currentText(),\
                                               self.expList,\
                                               self.proteinName.text(),\
                                               self.Targeting, self.pathway)

        for item in sorted(os.listdir(jsonpath)):
            if item.startswith(self.ExperimentID.currentText()) and item.endswith('json'):
                jsonfile = '{0}/{1}'.format(jsonpath, item)
                data = putMxlive.upload_labworks('BL-5C', jsonfile)
                print(data)
            else: pass

    def shft1Finish(self):
        jsonpath = func.shft1Done(self.xtalsToHarvest, self.ExperimentID.currentText(), self.proteinName.text(),self.pathway)
        for item in sorted(os.listdir(jsonpath)):
            if item.startswith(self.ExperimentID.currentText()) and item.endswith('json'):
                jsonfile = '{0}/{1}'.format(jsonpath, item)
                data = putMxlive.upload_labworks('BL-5C', jsonfile)
                print(data)
            else: pass
        
    def shft2Finish(self):
        jsonpath = func.shft2Done(self.xtalsToHarvest, self.ExperimentID.currentText(), self.proteinName.text(),self.pathway)
        for item in sorted(os.listdir(jsonpath)):
            if item.startswith(self.ExperimentID.currentText()) and item.endswith('json'):
                jsonfile = '{0}/{1}'.format(jsonpath, item)
                data = putMxlive.upload_labworks('BL-5C', jsonfile)
                print(data)
            else: pass

    def chemPlans(self):
        groupbox = QGroupBox()
        widget = QWidget()
        grid = QGridLayout()

        for item in self.expList:
            self.ExperimentID.addItem(item)

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
            self.selectLib.addItem(item)
        self.selectLib.currentTextChanged.connect( self.setCurrLibrary )

        self.selectLib.setCurrentIndex(0)
        self.chemVol.setRange(2.5, 300)
        self.chemVol.setSuffix('nl')
        self.chemVol.setSingleStep(2.5)
        self.chemVol.setDecimals(1)
        self.chemVol.setValue( float(10) )

        self.chemSite.setRange(0, 10)
        self.chemSite.setSuffix('point(s)')
        self.chemSite.setSingleStep(1)
        self.chemSite.setDecimals(0)
        self.chemSite.setValue( float(1) )

        #self.cryoWell.returnPressed.connect( self.estimateCryoWell )
        self.cryoWell.editingFinished.connect( self.estimateCryoWell )

        self.cryoVol.setRange(2.5, 500)
        self.cryoVol.setSuffix('nl')
        self.cryoVol.setSingleStep(2.5)
        self.cryoVol.setDecimals(1)
        self.cryoVol.setValue( float(100) )
        self.cryoVol.valueChanged.connect( self.zero )

        self.cryoSite.setRange(0, 10)
        self.cryoSite.setSuffix('point(s)')
        self.cryoSite.setSingleStep(1)
        self.cryoSite.setDecimals(0)
        self.cryoSite.setValue( float(1) )
        self.cryoSite.valueChanged.connect( self.zero )



        editButton = QPushButton("Edit")
        #editButton.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_MediaSkipForward')))
        editButton.clicked.connect(self.editChemPlans)
        saveButton = QPushButton("Save")
        saveButton.clicked.connect(self.saveChemPlans)

        grid.addWidget(QLabel("Experiment ID"),     0,0)
        grid.addWidget(self.ExperimentID,           0,1)
        grid.addWidget(QLabel("Protein Name"),      1,0)
        grid.addWidget(self.proteinName,            1,1)
        grid.addWidget(QLabel("Fragment Library"),  2,0)
        grid.addWidget(self.selectLib,              2,1)
        grid.addWidget(QLabel("     Soaking Vol"),  3,0)
        grid.addWidget(self.chemVol,                3,1)
        grid.addWidget(QLabel("   Soaking Sites"),  4,0)
        grid.addWidget(self.chemSite,               4,1)
        grid.addWidget(QLabel(" CryoProtect pID"),  5,0)
        grid.addWidget(self.cryoPlate,              5,1)
        grid.addWidget(QLabel("        Cryo well"), 6,0)
        grid.addWidget(self.cryoWell,               6,1)
        grid.addWidget(QLabel("     Transfer Vol"), 7,0)
        grid.addWidget(self.cryoVol,                7,1)
        grid.addWidget(QLabel("   Transfer Sites"), 8,0)
        grid.addWidget(self.cryoSite,               8,1)
        grid.addWidget(editButton,                  9,0)
        grid.addWidget(saveButton,                  9,1)
        #grid.setColumnStretch(0,3)
        #grid.setColumnStretch(1,0)
        #grid.setColumnStretch(2,0)



        groupbox.setLayout(grid)


        self.loadChemImg()
        return groupbox


    def saveChemPlans(self):
        check = []
        if self.selectLib.currentText() == 'none':
            warning = "No Library Selected"
            check.append(warning)
        if self.proteinName.text().strip() == '': 
            warning = "No Protein Name is given"
            check.append(warning)
        if self.proteinName.text().strip() == 'name':
            warning = "Check the Protein Name"
            check.append(warning)
        if self.chemVol.value() + self.cryoVol.value() == 0 or self.chemSite.value() + self.cryoSite.value() == 0:
            warning = "No Target Required"
            check.append(warning)
        
        if len(check) != 0:
            message = '\n'.join(check)
            react = QMessageBox.warning(self, "Check Parameters", message , QMessageBox.Yes)
            if react == QMessageBox.Yes:
                pass
        else:
            if self.planConfirmed == False:
                self.planConfirmed = True
                self.ExperimentID.setEnabled(False)
                self.proteinName.setReadOnly(True)
                self.selectLib.setEnabled(False)
                self.chemVol.setReadOnly(True)
                self.chemSite.setReadOnly(True)
                self.cryoPlate.setEnabled(False)
                self.cryoWell.setEnabled(False)
                self.cryoVol.setReadOnly(True)
                self.cryoSite.setReadOnly(True)
                #self.cryoSite.setStyleSheet('QComboBox{background:#efefef}')

                self.Targeting.clear()
                self.chemWell = 1
                self.chemWell_fin = 1
                self.cryoCnt = 1
                self.currentWell  = 1
                self.setCurrLibrary()            
                self.setCurrentWell()
                chemEachVol = misc.calEachSites( float(self.chemVol.value()), int(self.chemSite.value()))
                cryoEachVol = misc.calEachSites( float(self.cryoVol.value()), int(self.cryoSite.value()))
                self.EachVol = chemEachVol + cryoEachVol
                if self.ExperimentID.currentText() == 'New' or self.ExperimentID.currentText() == 'new':
                    self.NewExpID = func.createExpID(self.ExperimentID.currentText(), self.proteinName.text(), self.expList)
                    print('[ExperimentID] ', self.NewExpID)
                    self.expList.insert(1, self.NewExpID)
                    self.ExperimentID.clear()
                    for item in self.expList:
                        print(item)
                        self.ExperimentID.addItem(item)
                    self.ExperimentID.setCurrentText(self.expList[1])
            else:
                pass

    def editChemPlans(self):
        if self.planConfirmed == True:
            react = QMessageBox.information(self, "Really?", \
                                            "This action could reset you worked on.\nDo you want to continue?", QMessageBox.Cancel, QMessageBox.Yes)
            if react == QMessageBox.Cancel:
                pass
            elif react ==  QMessageBox.Yes:
                print('cleaning')
                self.Targeting.clear()
                self.chemWell = 1
                self.chemWell_fin = 1
                self.cryoCnt = 1
                self.currentWell  = 1
                self.setCurrLibrary()
                self.setCurrentWell()
                self.planConfirmed = False
                self.ExperimentID.setEnabled(True)
                self.proteinName.setReadOnly(False)
                self.selectLib.setEnabled(True)
                self.chemVol.setReadOnly(False)
                self.chemSite.setReadOnly(False)
                self.cryoPlate.setEnabled(True)
                self.cryoWell.setEnabled(True)
                self.cryoVol.setReadOnly(False)
                self.cryoSite.setReadOnly(False)
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
        print('loadChemInformation', self.chemWell)
        currentLibrary = self.selectLib.currentText()
        currLibPath = '{0}/{1}.csv'.format(self.libraryPath, currentLibrary)
        if currentLibrary != 'none' and os.path.isfile(currLibPath):
            #self.chemLib_info, LibPlates = misc.libraryToDict(currLibPath)
            #for i, item in enumerate(self.chemLib_info):
                #print(i,item)
            print('change', self.chemWell, self.chemLib_info[self.chemWell])
            self.view_chemLib    .setText(str(currentLibrary))
            self.view_chemWell   .setText(str(self.chemLib_info[self.chemWell]['PlateWell']))             
            self.view_chemID     .setText(str(self.chemLib_info[self.chemWell]['ChemID']))
            self.view_chemFormula.setText(str(self.chemLib_info[self.chemWell]['Formula']))
            self.view_chemSMILE  .setText(str(self.chemLib_info[self.chemWell]['SMILE']))
            self.view_chemConc   .setText(str('{0} mM'.format( self.chemLib_info[self.chemWell]['Conc'] )))
            self.view_chemMW     .setText(str('{0} g/mole'.format( self.chemLib_info[self.chemWell]['MW'] )))
            self.view_chemSolvent.setText(str(self.chemLib_info[self.chemWell]['Solvent']))
            self.view_chemVendor .setText(str(self.chemLib_info[self.chemWell]['Vendor']))
        #print('cchange', self.chemLib, self.chemWell)

        else:
            self.noneChemical()

    def loadChemImg(self):
        if self.selectLib.currentText() == 'none':
            imgFilePath = '/usr/local/XtalViewer/1.0/img/testtube.png'
        else:
            libname   = self.selectLib.currentText().split(' ')[0].strip()
            libheader = ['ChemID', 'Vendor', 'Library', 'PlateID', 'PlateWell', 'Formular', 'MW', 'Conc', 'Solvent', 'SMILE']
            chemName  = self.chemLib_info[self.chemWell]['ChemID']
            chemSmile = self.chemLib_info[self.chemWell]['SMILE']
            imgPath = '{0}/{1}/{2}'.format(self.chemsPath, libname, chemName)
            imgFilePath = '{0}/{1}.png'.format(imgPath, chemName)
            if not os.path.isfile(imgPath):
                chem.drawFormular(chemName, chemSmile, imgPath)
            else:
                pass
        print(imgFilePath)
        image = QImage(imgFilePath)
        if image.isNull():
            QMessageBox.information(self, "Image Viewer", "Cannot load %s." % imgFilePath)
            return
        pixmap = QPixmap(image)
        #pixmap = pixmap.scaledToWidth(s)
        pixmap = pixmap.scaledToHeight(200)
        self.CimgLabel.setPixmap(pixmap)
            
            
    def setCurrLibrary(self):
        expid = self.ExperimentID.currentText()
        currentLibrary = self.selectLib.currentText()
        currLibPath = '{0}/{1}.csv'.format(self.libraryPath, currentLibrary)
        if currentLibrary != 'none' and os.path.isfile(currLibPath):
            if expid in self.expOnWeb:
                doneList = getMxlive.successXtals(expid)[::-1]
                print(doneList)
                self.chemLib_info, self.chemPlates = misc.spareLibToDict(currLibPath, doneList)
            else:
                self.chemLib_info, self.chemPlates = misc.libraryToDict(currLibPath)

        self.loadChemInfo()
        self.loadChemImg()
        #self.estimateCryoWell()
        self.chemWell = 1

    def setCurrChemWell(self):        
        self.loadChemInfo()
        self.loadChemImg()

    def moveChemWell(self):
        if self.selectLib.currentText() != 'none':
            imgKey = '{0}_{1}'.format(self.currPlate_view.text(),self.currWell_view.text())
            if imgKey in self.Targeting:
                for item in self.Targeting[imgKey]:
                    print('item', item)
                    prefix   = item[0].split(' ')[0].strip() 
                    #no       = point[0].split(' ')[1].strip()
                    #chemPID  = point[0].split(' ')[2].strip()
                    #chemWell = point[0].split(' ')[3].strip()
                    if prefix == 'chem' or prefix == 'Chem' or prefix == 'CHEM':
                        no       = item[0].split(' ')[1].strip()
                        self.chemWell = int(no)
                        self.setCurrChemWell()
                        print("[{0}] has chem Target".format(imgKey))
                    else:
                        print("[{0}] No chem Target in this well".format(imgKey))
            else:
                self.chemWell = len(self.Targeting)+ 1
                print("[{0}] No Target in this well".format(imgKey))
            self.setCurrChemWell()

        else: pass

    def moveNextChemWell(self):
        #time.sleep(3)
        if self.selectLib.currentText() != 'none':
            if self.chemWell == len( self.chemLib_info ) - 1:
                print('[Note] Last Chem in this Library')
            else:
                imgKey = '{0}_{1}'.format(self.currPlate_view.text(),self.currWell_view.text())
                if imgKey in self.Targeting:
                    self.chemWell += 1
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
        if self.selectLib.currentText() != 'none':
            libraryScale = len(self.chemLib_info)-1
            cryoVol = self.cryoVol.value()
            userWells = func.validateWell(self.cryoWell.text())
            prepWells = func.wellsToPrep(libraryScale, self.cryoWellVol, cryoVol, userWells)
            self.cryoWell.setText(', '.join(prepWells))
            """
            grid384 = []
            for i in range(1,25):
                for j in range(65, 81):
                    w = '{0}{1}'.format(chr(j),str(i).zfill(2))
                    grid384.append(w)
            libraryScale = len(self.chemLib_info)-1
            validVol = 2800
            chambers = math.ceil( float(self.cryoVol.value()) * libraryScale / validVol )
            times = math.trunc( validVol / float( self.cryoVol.value() ) )
            userWells = func.validateWell(self.cryoWell.text())
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
                        self.cryoWell.setText(', '.join(userWells))
                        print('needed wells:', chambers, 'userWell++', userWells)               
            """
        else: pass
        



    def zero(self):
        print("I'm not anything")

    def imageClickEvent(self, event):
        #text = "LeftClick: x={0}, y={1}, global = {2},{3}".format(event.x()-zero[0], event.y()-zero[1], event.globalX(), event.globalY())
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
            if self.selectLib.currentText() == 'none':
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

            else:
                text = "RightClick: x={0}um, y={1}um in {2}_{3}".format(round(xMeterPosition,0), round(yMeterPosition,0), pID, well)
                self.window.statusbar.showMessage(text)
                if  self.planConfirmed == True:
                    if self.chemSite.value() != 0:
                        chemPID = self.chemLib_info[self.chemWell]['PlateID'].strip()
                        chemWell = self.chemLib_info[self.chemWell]['PlateWell'].strip()
                        chem = 'chem {0} {1} {2}'.format(self.chemWell, chemPID, chemWell)
                        chem_points = [chem, round(xMeterPosition,0),round(yMeterPosition,0), xPixcelPosition, yPixcelPosition]
                    else: pass
                    if self.cryoSite.value() != 0:
                        cryoPID = self.cryoPlate.text().strip()
                        cryoWells = self.cryoWell.text().split(',')
                        cryoWell  = func.whichCryoWell(cryoWells, self.cryoWellVol, self.cryoVol.value(), self.cryoSite.value(), self.cryoCnt)
                        cryo = 'cryo {0} {1} {2}'.format(self.cryoCnt, cryoPID, cryoWell)
                        cryo_points = [cryo, round(xMeterPosition,0),round(yMeterPosition,0), xPixcelPosition, yPixcelPosition]
                    else: pass

                    if self.chemSite.value() == 0 and self.cryoSite.value() == 0:
                        print('[ Warning ] No  targets to select')
                    elif self.chemSite.value() != 0 and self.cryoSite.value() == 0:
                        if imgKey not in self.Targeting:
                            self.Targeting[imgKey] = [ chem_points ]
                        else:
                            self.Targeting[imgKey].append( chem_points )
                        self.loadImage()

                    elif self.chemSite.value() == 0 and self.cryoSite.value() != 0:
                        if imgKey not in self.Targeting:
                            self.Targeting[imgKey] = [ cryo_points ]
                            self.cryoCnt += 1
                        else:
                            self.Targeting[imgKey].append( cryo_points )
                            self.cryoCnt += 1
                        self.loadImage()
                   
                    else:
                        if imgKey not in self.Targeting:
                            self.Targeting[imgKey] = [ chem_points ]
                            #self.loadImage()
                        elif len(self.Targeting[imgKey]) < self.chemSite.value():
                            self.Targeting[imgKey].append( chem_points )
                            #self.loadImage()
                        elif len(self.Targeting[imgKey]) == self.chemSite.value():
                            self.Targeting[imgKey].append( cryo_points )
                            #self.loadImage()
                            self.cryoCnt += 1
                        elif len(self.Targeting[imgKey]) < self.chemSite.value()+self.cryoSite.value():
                            self.Targeting[imgKey].append( cryo_points )
                            #self.loadImage()
                            self.cryoCnt += 1
                        else: pass
                        self.loadImage()
                    self.doneMove()
                else:
                    print('[ Warning ] Check Parameters and Save them')
        print(self.Targeting)
        


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
