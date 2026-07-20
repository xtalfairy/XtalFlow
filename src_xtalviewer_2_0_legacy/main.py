# -*- coding: utf-8 -*-
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
#from custom_table import PlateTableView
import csv, math, time
import numpy as np
import random
#import matplotlib.pyplot as plt
from datetime import datetime
#from mpl_toolkits.mplot3d import Axes3D
#from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from utils import misc, func, chem, adpt
from utils import dialogs
from utils.client import getMxlive, putMxlive


###[General Definitions]
XTALVIEWER_PATH = '/usr/local/XtalViewer/2.0'
USERHOME_PATH   = '/data/users/{0}'.format(misc.getUsername())
PATHS           = { 'rmserver' : '/smbmount/rmserver/RockMakerStorage/WellImages',\
                    'echo650'  : '/smbmount/echo650',       \
                    'shifter1' : '/smbmount/shifter1',      \
                    'shifter2' : '/smbmount/shifter2',      \
                    'library'  : XTALVIEWER_PATH + '/chems', \
                    'cellar'   : USERHOME_PATH + '/FBDD/XV_log',\
                    'icons'    : XTALVIEWER_PATH+'/img/icons'}

print(USERHOME_PATH)

class index(QMainWindow):

    def __init__(self):
        super().__init__()
        main = MyApp(self)
        self.setCentralWidget(main)

        self.setWindowTitle('Xtal Viewer')
        self.setWindowIcon(QIcon(XTALVIEWER_PATH+'img/fairy_03.png'))
        self.weight = 1200
        self.height = 800
        self.left = 500
        self.top  = 0
        self.setGeometry(self.left, self.top, self.weight, self.height)


        self.statusbar = self.statusBar()
        self.setMouseTracking(True)


        #Enrich Statusbar : 20221011
        #self.statusBar().reformat()
        message = "Hello {} :)".format(misc.getUsername().upper())
        self.distance = QLabel('   0 um')
        self.nWells   = QLabel('0'.rjust(4,' '))
        self.statusBar().showMessage(message)
        self.statusBar().setStyleSheet('border: 0; background-color: #FFF8DC;')
        self.statusBar().setStyleSheet("QStatusBar::item {border: none;}") 
        self.statusBar().addPermanentWidget(VLine())
        self.statusBar().addPermanentWidget(QLabel('distance:'))
        self.statusBar().addPermanentWidget(self.distance)
        self.statusBar().addPermanentWidget(VLine())
        self.statusBar().addPermanentWidget(QLabel('selected well'))
        self.statusBar().addPermanentWidget(self.nWells)
        #self.statusBar().addPermanentWidget(VLine())







        self.show()

class VLine(QFrame):
    # a simple VLine, like the one you get from designer
    def __init__(self):
        super(VLine, self).__init__()
        self.setFrameShape(self.VLine|self.Sunken)


class MyApp(QWidget):

    def __init__(self, window=None):
        super().__init__()
        self.initUI()
        self.window = window



    def closeEvent(self, event):
        pass

    def initUI(self):
        self.mainWidget = QWidget()
        self.mainWidget.closeEvent = self.closeEvent        


        ### [Reading Image Configuration]
        self.ImageTypeDic = {'Visible':'profileID_1', 'Contrast':'profileID_3?', 'HighRes':'profileID_5', 'Polarize':'profileID_8'}
        self.ImageType    = QComboBox(self)
        self.PlateType    = QComboBox(self)


        ### [Take The Well Position]
        self.wellRadius  = 1400       #A BabyDrop Radius  1.4mm (1400um)
        self.guideRadius = 485        #Radius of Centering Guide Circle (485px)
        self.guideShift  = [150, 50]
        #self.guideShift0 = [150, 50]
        self.colOffset = [0,0]  #To Calibrate Horizontal direction motor error ex.[5.2, -1.7]
        self.rowOffset = [0,0]  #To Calibrate  Vertical  direction motor error ex.[2.2, 3.7]
        #30 = 5nl, 45 = 10nl
        #self.pointRadius = 30*2 /(self.imgScale.value()/100)
        self.xCenter  = QSlider(Qt.Horizontal)
        self.yCenter  = QSlider(Qt.Horizontal)
        self.imgScale = QDoubleSpinBox()
        self.refPoints = [] # To estimate a distance between two points
        self.restoreButton = QPushButton('Restore')
        self.fixCurrButton = QPushButton('Save')
        self.restoreButton.clicked.connect( self.defaultScreenSetting )
        self.fixCurrButton.clicked.connect( self.fixScreenSetting )
        

        ####Variables for [Save Personalized Configuration]
        self.ConfigPath    = USERHOME_PATH + '/FBDD/config'
        self.screenConfig0 = {'xShift':0, 'yShift':0, 'imgScale':50}
        self.screenConfig1 ={}
        self.OrgImage      = [0,0]

        ###Variables for [Object Plates]
        self.objectPlates = QLineEdit()
        self.tableview = QTableView()
        self.tableview_model = QStandardItemModel()
        self.tableview.setSortingEnabled(True)
        self.plates_info = {}


        ###Variables for [Plate Navigator]
        self.currentPlate = QLineEdit('none')
        self.currentWell  = QLineEdit()
        self.lastWell     = ''                #to restore when user give wrong well name to self.currentWell
        self.lastPlate    = ''
        self.currWellNo   = 1
        self.homeButton = QPushButton()
        self.prevButton = QPushButton()
        self.nextButton = QPushButton()
        self.lastButton = QPushButton()
        self.undoButton = QPushButton()
        self.eraserFlag = False
        



        ###Common Vartiables for all type experiments
        self.crystname = QLineEdit()
        self.divisions = QSpinBox()
        self.targetVol = QDoubleSpinBox()
        #self.divisions.setValue(2)
        self.divisions.setRange(1,10)
        self.targetVol.setRange(2.5, 300)
        self.targetVol.setSingleStep(2.5)
        self.targetVol.setDecimals(1)
        self.targetVol.setSuffix('nl')



        ###Variables for [evaluatePlans]
        #self.crystname = QLineEdit()
        self.solvents  = QLineEdit("DMSO, EG")
        self.incuTime  = QLineEdit("0, 1h, 2h, 6h")
        self.solventVol= QLineEdit("5, 15, 30")
        self.cryoVol   = QLineEdit("80, 100")
        self.replica   = QSpinBox()
        self.replica.setRange(1,100)
        self.replica.setValue(3)

        self.pilot_tableview = QTableView()
        self.pilot_tableview.setSortingEnabled(True)
        self.pilot_tableview_model = QStandardItemModel()
        self.requestMessage = QTextEdit()
        self.add_evalueplan_button = QPushButton('Add')
        self.undo_evalueplan_button = QPushButton('Undo')
        self.reset_evalueplan_button = QPushButton('Reset')


        ###Variables for [screenPlans]
        self.libraryInfo = []
        self.regionLib = QLineEdit()

        ###Variables for [cryoPlans]
        self.cryoStrategy = QComboBox()
        self.cryoSites  = QSpinBox()
        self.cryoVolume = QDoubleSpinBox()
        


        #[Final Result]
        self.targetPoints = {}
        self.targetCounts = {}   #Count Targeted Wells
        ###Variables for [XtalViewer Configuration]
        #self.ImageProfile = "profileID_1"
        #self.ImageTypeDic = {'Visible':'1', 'Contrast':'3?', 'HighRes':'5', 'Polarize':'8'}


        ### Variables for Import/Export Functions
        importTargetsBtn = QPushButton("Import Targets")
        exportTargetsBtn = QPushButton("Export Targets")
        saveTargetsBtn   = QPushButton("Save Targets as csv")
        saveWSheetsBtn   = QPushButton("Save Worksheets")
        uploadDbBtn      = QPushButton("Upload to MxLIVE")
        importTargetsBtn.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_DialogOpenButton')))
        #exportTargetsBtn.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_FileDialogStart')))
        exportTargetsBtn.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_DialogOkButton')))
        saveTargetsBtn.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_DialogSaveButton')))
        saveWSheetsBtn.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_DialogSaveButton')))
        uploadDbBtn.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_ArrowUp')))
        importTargetsBtn.clicked.connect(self.importTargets)
        exportTargetsBtn.clicked.connect(self.exportTargets)



        ####Variavles for [???]
        self.iconColor    = '333333'





        leftPanel = QWidget()
        leftgrid = QGridLayout()
        leftgrid.addWidget(self.Configuration(), 0,0)
        leftgrid.addWidget(self.imgNavigator(),  1,0)
        leftgrid.addWidget(self.ObjectPlates(),  2,0)
        leftgrid.setAlignment(Qt.AlignLeft)
        leftPanel.setLayout(leftgrid)

        #rightPanel = QWidget()
        #rightgrid = QGridLayout()
        ##rightgrid.addWidget(self.imgNavigator(),   0,0)
        #rightgrid.addWidget(self.evaluePlans(),    0,0)
        #rightgrid.addWidget(self.screenPlans(),    1,0)
        #rightgrid.addWidget(self.cryoPlans(),      2,0)
        #rightgrid.addWidget(self.requestBox(),     3,0)
        #rightgrid.setAlignment(Qt.AlignTop)
        #rightPanel.setLayout(rightgrid)


        # 기존 rightPanel과 rightgrid 대신 사용할 Tab Widget 생성
        self.rightTab = QTabWidget()
        self.rightTab.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        #self.rightTab.setMaximumHeight(300)
        # 각 탭에 넣을 위젯들을 생성하고, 기존 rightgrid의 요소들을 분리하여 탭으로 구성
        evalueTab = QWidget()
        evalueLayout = QVBoxLayout()
        evalueLayout.addWidget(self.evaluePlans())
        evalueTab.setLayout(evalueLayout)
        #evalueLayout.setContentsMargins(0, 0, 0, 0)

        
        screenTab = QWidget()
        screenLayout = QVBoxLayout()
        #screenLayout.setContentsMargins(0, 0, 0, 0)
        screenLayout.addWidget(self.screenPlans())
        screenTab.setLayout(screenLayout)
        
        cryoTab = QWidget()
        cryoLayout = QVBoxLayout()
        #cryoLayout.setContentsMargins(0, 0, 0, 0)
        cryoLayout.addWidget(self.cryoPlans())
        cryoTab.setLayout(cryoLayout)

        outputWidget = QWidget()
        outputLayout = QGridLayout()
        outputLayout.addWidget(importTargetsBtn, 0,0)
        outputLayout.addWidget(exportTargetsBtn, 0,1)
        outputLayout.addWidget(saveTargetsBtn,   1,0)
        outputLayout.addWidget(saveWSheetsBtn,   1,1)
        outputLayout.addWidget(uploadDbBtn,      2,0,2,2)
        outputLayout.setContentsMargins(0,0,0,0)
        outputWidget.setLayout(outputLayout)
        outputWidget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)  
        #outputWidget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Expanding)
        
        # QTabWidget에 탭 추가
        self.rightTab.addTab(evalueTab, "Evalue Plans")
        self.rightTab.addTab(screenTab, "Screen Plans")
        self.rightTab.addTab(cryoTab, "Cryo Plans")

        # 기존 rightPanel 대체
        rightPanel = QWidget()
        rightLayout = QVBoxLayout()
        rightLayout.setContentsMargins(0, 0, 0, 0)
        rightLayout.addWidget(self.rightTab)
        rightLayout.addWidget(outputWidget)
        rightPanel.setLayout(rightLayout)



        grid = QGridLayout()
        grid.addWidget(leftPanel,           0,0)
        grid.addWidget(self.Viewer(),       0,1)
        grid.addWidget(rightPanel,          0,2)
        #grid.addWidget(requestTab,          1,2)
        grid.setAlignment(Qt.AlignTop)
        self.setLayout(grid)






    def Configuration(self):
        groupbox = QGroupBox('Xtal Viewer Configure')
        grid = QGridLayout()

        ### Validate Given Configuration record
        screenConfig = '{0}/screen'.format(self.ConfigPath)


        if not os.path.isfile(screenConfig):
            print('[ViewerSetup] No Screen Configuration')
            self.screenConfig1 = self.screenConfig0
        else:
            try:
                self.screenConfig1 = misc.configToDict(screenConfig)
                xShift0 = int(self.screenConfig1.get('xShift', 0))
                yShift0 = int(self.screenConfig1.get('yShift', 0))
                imgScale0 = float(self.screenConfig1.get('imgScale', 1.0))
                #self.guideShift1[0] = self.guideShift0[0] + xShift0
                #self.guideShift1[1] = self.guideShift0[1] + yShift0
                print('[ViewerSetup] User-defined Configuration was found')
            except (KeyError, ValueError) as e:
                self.screenConfig1 = self.screenConfig0
                print(f'[ViewerSetup] Error in Configuration: {e}')
                print('[ViewerSetup] Viewer Setup as Default opt')



        ### Make GUI for Configuration
        imgTypes = sorted(self.ImageTypeDic.items(), key=lambda item:item[1])
        for key,val in imgTypes:
            self.ImageType.addItem(key)
        #self.ImageType.currentTextChanged.connect(self.selectImageType)
        imageprofile = self.ImageTypeDic[self.ImageType.currentText()]
        self.PlateType.addItem('SwissCI-MRC-3d')
        self.PlateType.addItem('SwissCI-MRC-2d')

        self.xCenter.setRange(-50,50)
        self.xCenter.move(1,1)
        self.xCenter.setTickPosition(QSlider.TicksBothSides)
        self.xCenter.setTickInterval(5)
        self.xCenter.setSingleStep(1)
        #self.xCenter.setValue( int(self.screenConfig1['xShift']) )
        self.xCenter.setValue(int(self.screenConfig1.get('xShift', 0)))
        self.xCenter.sliderReleased.connect(self.moveCenterX)

        self.yCenter.setRange(-50,50)
        self.yCenter.move(1,1)
        self.yCenter.setTickPosition(QSlider.TicksBothSides)
        self.yCenter.setTickInterval(5)
        self.yCenter.setSingleStep(1)
        #self.yCenter.setValue( int(self.screenConfig1['yShift']) )
        self.yCenter.setValue(int(self.screenConfig1.get('yShift', 0)))
        self.yCenter.sliderReleased.connect(self.moveCenterY)


        grid.addWidget(QLabel('ImageType'),      0,0,1,2)
        grid.addWidget(self.ImageType,           0,2,1,4)
        grid.addWidget(QLabel('PlateType'),      1,0,1,2)
        grid.addWidget(self.PlateType,           1,2,1,4)

        grid.addWidget(QLabel("Center X-axis"),    2,0,1,2)
        grid.addWidget(self.xCenter,               2,2,1,4)
        grid.addWidget(QLabel("Center Y-axis"),    3,0,1,2)
        grid.addWidget(self.yCenter,               3,2,1,4)
        grid.addWidget(self.restoreButton,         4,0,1,3)
        grid.addWidget(self.fixCurrButton,         4,3,1,3)        

        groupbox.setMaximumHeight(200)
        groupbox.setMaximumWidth(250)
        groupbox.setLayout(grid)
        return groupbox



    def resetScreenSetting(self):
        self.xCenter.setValue( int(self.screenConfig1['xShift']) )
        self.yCenter.setValue( int(self.screenConfig1['yShift']) )
        self.imgScale.setValue( float(self.screenConfig1['imgScale']) )
        self.loadImage()
        return 0

    def defaultScreenSetting(self):
        self.screenConfig1 = self.screenConfig0
        xShift = self.screenConfig1['xShift']
        yShift = self.screenConfig1['yShift']
        imgScale = self.screenConfig1['imgScale']
        #self.guideShift1[0] = self.guideShift0[0] + xShift
        #self.guideShift1[1] = self.guideShift0[1] + yShift
        self.resetScreenSetting()
        self.fixScreenSetting()
        return 0
    def fixScreenSetting(self):
        if not os.path.isdir(self.ConfigPath):
            misc.createFolder(self.ConfigPath)
        #print(self.window.sizeHint().width(), self.window.size().height())
        screenConfig = '{0}/screen'.format(self.ConfigPath)
        f = open(screenConfig, 'w')
        f.write( "xShift:{0}\n".format( str(self.xCenter.value()) ) )
        f.write( "yShift:{0}\n".format( str(self.yCenter.value()) ) )
        f.write( "imgScale:{0}\n".format( str(self.imgScale.value()) ) )
        f.close()
        print('[ViewerSetup] Current X,Y/ImgScale are Fixed', self.xCenter.value(),self.yCenter.value(), self.screenConfig1)
        return 0

    def selectImageType(self):
        return 0
    def targetSites(self):
        return 0
    def imageAdjustment(self):
        return 0

    def moveCenterX(self):
        #center = self.guideShift[0] + self.xCenter.value()
        #self.guideShift1[0] = center
        try:
            self.loadImage()
        except IndexError as e:
            pass

        return 0

    def moveCenterY(self):
        #center = self.guideShift[1] + self.yCenter.value()
        try:
            self.loadImage()
        except IndexError as e:
            pass
        return 0




    def ObjectPlates(self):
        groupbox = QGroupBox('Object Plates')
        grid = QGridLayout()

        enterload = QPushButton('Load', self)
        enterload.clicked.connect(self.loadPlates)
        self.objectPlates.returnPressed.connect(self.loadPlates)
        self.tableview.setModel(self.tableview_model)
        self.tableview.setSelectionBehavior(QAbstractItemView.SelectRows)
        #Plate list DoubleClick Event
        self.tableview.doubleClicked.connect( self.setCurrentPlate )

        delete = QPushButton('Delete')
        delete.clicked.connect( self.deletePlate )
        refresh = QPushButton('Refresh')
        refresh.clicked.connect( self.refreshPlates )
        clear = QPushButton('Clear')
        clear.clicked.connect( self.clearPlates )

        grid.addWidget(QLabel('Protein'),   0,0,1,2)
        grid.addWidget(self.crystname,      0,2,1,4)
        grid.addWidget(QLabel('Plates ID'), 1,0,1,2)
        grid.addWidget(self.objectPlates,   1,2,1,3)
        grid.addWidget(enterload,           1,5,1,1)
        grid.addWidget(self.tableview,      2,0,1,6)
        grid.addWidget(refresh,             3,0,1,2)
        grid.addWidget(delete,              3,2,1,2)
        grid.addWidget(clear,               3,4,1,2)

        groupbox.setMaximumWidth(250)
        groupbox.setLayout(grid)
        return groupbox

    def checkStoragePath(self, StoragePath):
        if os.path.exists(StoragePath): 
            return True
        else:
            return False


    def processPlateIDs(self):
        result = []  # 결과를 담을 리스트
        plates = self.objectPlates.text().split(',')  # 쉼표로 구분하여 분리

        for plate in plates:
            plate = plate.strip()  # 양쪽 공백 제거
            if '-' in plate or '~' in plate:  # 범위 구분자가 있는지 확인
                delimiter = '-' if '-' in plate else '~'  # 실제 사용된 구분자를 선택
                try:
                    start, end = map(int, plate.split(delimiter))  # 범위 시작과 끝 추출
                    if start > end:  # 큰 수~작은 수는 잘못된 입력으로 처리
                        dialogs.show_warning(f"잘못된 범위 입력: {plate}")  # 메시지 창 호출
                        continue
                    result.extend(range(start, end + 1))  # 범위를 리스트에 추가
                except ValueError:
                    dialogs.show_warning(f"Wrong Value: {plate}")
                    continue  # 변환에 실패한 경우 무시
            else:
                try:
                    # 정수 값인지 확인 후 추가
                    result.append(str ( int(plate)) )
                except ValueError:
                    wrongs.append(plate)
                    continue  # 정수가 아닌 경우 무시

        return result



    def loadPlates(self):
        rawList = []
        wrongPlates = []
        imageProfile = self.ImageTypeDic[self.ImageType.currentText()]
    
        # 플레이트 ID가 입력되지 않은 경우 동작 하지 않음
        if not self.objectPlates.text():
            return
        # Storage path가 존재하는지 확인 (마운트 끊긴 경우 알림)
        if not self.checkStoragePath(PATHS['rmserver']):
            dialogs.show_warning(f"Image Server is Disconnected") 
            return
    
        #rawList = self.objectPlates.text().split(',')
        rawList = self.processPlateIDs()
    
        # Plate 정보를 로드하고 요약 저장
        self.loadPlateInfo(rawList)
    
        # 잘못된 plate 처리
        self.handleInvalidPlates(wrongPlates)
        self.refreshPlates()
    
    def loadPlateInfo(self, rawList):
        # 이용자가 입력한 plate id 정보는 processPlateIDs 함수를 통해 정수로 변환되어 돌아옵니다
        wrongPlates = []
        """주어진 rawList에서 Plate 정보를 로드하고 plates_info에 저장합니다."""
        for item in rawList:
            pID = item
            try:
                # self.plates_info[pID] = {'plate_id': '1535', 'drops': 192, 'eof': 0, 'selected': 0, \
                #                          'info': {'A01a': {'wellNo': 'wellNum_1', 'Subwell': 1, 'imgPath': '.../batchID_9727/wellNum_1/profileID_1/d1_r208903_ef.jpg'},
                #                                  {'A01c': { ...} }, ... }} 
                info = func.readPlate(int(pID), 'SwissCI-MRC-3d', self.ImageTypeDic[self.ImageType.currentText()], PATHS['rmserver'], PATHS['cellar'])
                if info:
                    self.plates_info[pID] = info
                    print(f"[loadPlate] Get Plate info: {pID}", type(pID))
                else:
                    wrongPlates.append(pID)
            except Exception as e:
                print(f"[loadPlate] Failed to survey plate {pID}: {e}")
                wrongPlates.append(pID)
        #[!!!]  문제가 있는 pID를 언제 처리/알림할 지 결정해야 함
        if len(wrongPlates) > 0:
            dialogs.show_warning(f"Cannot Load This plate(s): {wrongPlates}")
        return wrongPlates 


    def loadPlates0(self):
        rawList = []
        wrongPlates = []
        imageProfile = self.ImageTypeDic[self.ImageType.currentText()]
        
        # Storage path와 입력값이 유효한지 확인
        if not self.checkStoragePath(PATHS['rmserver']) or not self.objectPlates.text():
            return
    
        rawList = self.objectPlates.text().split(',')
        #target_cnt = func.checkSelected(self.targetPoints)
        
        # Plate 정보를 로드하고 요약 저장
        for item in rawList:
            pID = item.strip()
            try:
                self.plates_info[pID] = func.readPlate(pID, 'SwissCI-MRC-3d', imageProfile, PATHS['rmserver'], PATHS['cellar'])
            except Exception as e:
                print(f"[Error] Failed to survey plate {pID}: {e}")
                wrongPlates.append(pID)
    
        #만약 반환된 survey에서 imgpath가 하나도 없으면 잘못된 플레이트
        # 잘못된 plate 처리
        self.handleInvalidPlates(wrongPlates)
        self.refreshPlates()
    
    def handleInvalidPlates(self, wrongPlates):
        """잘못된 plate ID를 처리하는 함수"""
        if not wrongPlates:
            return
    
        react = QMessageBox.warning(self, "Check plateID", '\n'.join(wrongPlates), QMessageBox.Yes)
        if react == QMessageBox.Yes:
            print('[ Warning ] Invalid Plates confirmed by user')
    
    def clearPlates(self):
        self.tableview_model.removeRows(0, self.tableview_model.rowCount())
        self.tableview_model.removeColumns(0, self.tableview_model.columnCount())
        self.plates_info.clear()
        self.targetPoints.clear()
        return 0

    def refreshPlates(self):
        self.tableview_model.removeRows(0, self.tableview_model.rowCount())
        self.tableview_model.removeColumns(0, self.tableview_model.columnCount())
        target_cnt = func.checkSelected(self.targetPoints)
        i = 0
        for plate, descript in sorted(self.plates_info.items()):
           cellwidget = QWidget()
           layout = QHBoxLayout(cellwidget)
           layout.setAlignment(Qt.AlignCenter)
           layout.setContentsMargins(0, 0, 0, 0)
           cellwidget.setLayout(layout)
           #state['selected'] = len( [ key for key,val in self.targetPoints.items() if key.startswith(plate) ] )
           #state['selected'] = func.checkSelected(plate, self.targetPoints)
           #column00 = QStandardItem()
           column01 = QStandardItem(str(plate))
           column02 = QStandardItem(str(descript['drops']))
           #column03 = QStandardItem(str(descript['selected']))
           column03 = QStandardItem( str(self.targetCounts.get(plate, 0)) )
           self.tableview_model.setHorizontalHeaderLabels(['plateID', 'Drops', 'Select'])
           #self.tableview.setIndexWidget(self.tableview_model.index(i, 0), cellwidget)
           self.tableview_model.setItem(i, 0, column01)
           self.tableview_model.setItem(i, 1, column02)
           self.tableview_model.setItem(i, 2, column03)
           self.tableview.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
           self.tableview.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
           self.tableview.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
           column01.setTextAlignment(Qt.AlignHCenter|Qt.AlignVCenter|Qt.AlignCenter)
           column02.setTextAlignment(Qt.AlignHCenter|Qt.AlignVCenter|Qt.AlignCenter)
           column03.setTextAlignment(Qt.AlignHCenter|Qt.AlignVCenter|Qt.AlignCenter)
           self.tableview.setIndexWidget(self.tableview_model.index(i, 0), cellwidget)
           i += 1
        self.tableview.setEditTriggers(QAbstractItemView.NoEditTriggers)
        #self.setCurrentPlate()
        return 0

    def deletePlate(self):
        #remove selected item from tableview
        #remove selected item from self.plateSummary(?)
        #Indirect way :(
        selectedRow = self.tableview.currentIndex().row()
        selectedPID = self.tableview_model.item(selectedRow, 0).text()
        del(self.plates_info[selectedPID])
        del(self.targetPoints[selectedPID])
        #print(self.targetPoints)
        #for item in [key for key in self.targetPoints.keys() if key.startswith(selectedPID)]:
        #    del(self.targetPoints[item])
        #print(self.targetPoints)
        
        self.refreshPlates()
        return 0


    def setCurrentPlate(self):
        row = self.tableview.currentIndex().row()
        plate_id = self.tableview_model.item(row, 0).text()
    
        if not plate_id:  # plate_id가 유효한지 검사
            QMessageBox.warning(self, "Warning", "No plate selected.")
            return
    
        self.currentPlate.setText(plate_id)
        self.lastPlate = plate_id
        self.currWellNo = 1
        self.setCurrentWell()
        try:
            self.setCurrentWell()  # setCurrentWell 호출 시 예외 처리
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to set current well: {e}")
    
        return 0



    def setCurrentWell(self):
        curr_plate = str(self.currentPlate.text().strip())
        # curr_plate가 유효한지 먼저 확인합니다.
        if curr_plate not in self.plates_info:
            print(f"[Error@setCurrentWell] Plate ID '{curr_plate}' is not valid.")
            return
    
        wells = sorted(self.plates_info[curr_plate]['info'].keys())
        
        # wells가 비어 있는지 확인합니다.
        if not wells:
            print(f"[Error@setCurrentWell] No wells available in plate '{curr_plate}'.")
            return
    
        try:
            curr_well = wells[self.currWellNo - 1]
            self.currentWell.setText(curr_well)
            self.lastWell = curr_well   # 사용자가 잘못된 웰을 입력했을 때 복구를 위해 저장
            
            # 이미지 로드 전에 imgPath 유효성 확인
            well_info = self.plates_info[curr_plate]['info'].get(curr_well)
            if well_info and well_info.get('imgPath'):
                self.loadImage()
                print(f'[CurrentWell] pID{curr_plate} {curr_well}')
            else:
                print(f"[Warning@setCurrentWell] No image available for well '{curr_well}' in plate '{curr_plate}'.")
    
        except IndexError:
            print(f"[Error@setCurrentWell] Invalid well number: {self.currWellNo}. There are only {len(wells)} wells.")




    def zero(self):
        sender = self.sender()
        print(sender.currentText())
        return 0


    def Viewer(self):
        Viewer = QWidget()
        self.pathLineEdit = QLineEdit()
        self.imageLabel = QLabel()
        self.imageLabel.setPixmap(QPixmap())
        self.imageLabel.mousePressEvent = self.imageClickEvent

        self.OrgImgage = [0,0]
        self.ImgWidth0 = 918
        self.ImgHeight0 = 768
        self.ImgWidth1 = self.ImgWidth0
        self.ImgHeight1 = self.ImgHeight0
        self.dImgWidth = 0
        self.dImgHeight = 0
        self.imageLabel.setFixedWidth(self.ImgWidth0)
        self.imageLabel.setFixedHeight(self.ImgHeight0)

        self.pixmap = QPixmap()
        self.pixmap2 = QPixmap()

        grid = QGridLayout()
        #grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        grid.addWidget(self.imageLabel, 0,0)
        Viewer.setLayout(grid)
        return Viewer


    def loadImage(self):
        plate_id = str(self.currentPlate.text())
        well_id  = self.currentWell.text()
        well_info = self.plates_info[plate_id]['info'][well_id]
        print(self.currWellNo, well_id)
        imgPath = well_info.get('imgPath')
        img_descript = f"{imgPath.split('/')[6]} {imgPath.split('/')[8]} {imgPath.split('/')[-1].split('_')[0]}"
        #if not imgPath:
        #    QMessageBox.information(self, "Image Viewer", "No image available for this well.")
        #    return
        #else: 
        #    img_descript = f"{imgPath.split('/')[6]} {imgPath.split('/')[8]} {imgPath.split('/')[-1].split('_')[0]}"
        
        image = self.loadAndInitializeImage(imgPath)
        if image is None:
            return
    
        self.calculateImageScale()
        self.drawImage(image, img_descript)



    # For Image DEBUG
    def showImageInNewWindow(self, image_or_pixmap):
        """Create a new window and display the image or pixmap."""
        # QDialog 생성
        dialog = QDialog(self)
        dialog.setWindowTitle("Image for DEBUG")
    
        # QLabel 생성
        imageLabel = QLabel(dialog)
    
        # 전달된 인자가 QImage인지 QPixmap인지 확인
        if isinstance(image_or_pixmap, QPixmap):
            pixmap = image_or_pixmap  # 이미 QPixmap이면 그대로 사용
        elif isinstance(image_or_pixmap, QImage):
            pixmap = QPixmap.fromImage(image_or_pixmap)  # QImage를 QPixmap으로 변환
        else:
            QMessageBox.warning(self, "Invalid Input", "The provided input is neither a QPixmap nor a QImage.")
            return  # 잘못된 입력이면 함수 종료
    
        imageLabel.setPixmap(pixmap)
    
        # 레이아웃 설정
        layout = QVBoxLayout()
        layout.addWidget(imageLabel)
        dialog.setLayout(layout)
    
        # 창 크기를 이미지 크기에 맞추기
        dialog.resize(pixmap.width(), pixmap.height())
    
        # 창 띄우기
        dialog.exec_()



    def loadAndInitializeImage(self, imgPath):
        """Load the image and initialize if successful."""
        image = QImage(imgPath)
        if image.isNull():
            QMessageBox.information(self, "Image Viewer", f"Cannot load {imgPath}.")
            return None
        self.pathLineEdit.setText(imgPath)
        self.OrgImage[0] = image.width()
        self.OrgImage[1] = image.height()
        
        return image
    
    def calculateImageScale(self):
        """Calculate the image dimensions based on the current scale."""
        # 창 크기를 변형할 수 있는 기능을 언젠가 회복시킬거에요
        self.imgScale.setValue(75)
        scaleFactor = self.imgScale.value() / 100
        self.ImgWidth1 = self.OrgImage[0] * scaleFactor
        self.ImgHeight1 = self.OrgImage[1] * scaleFactor
    
    def drawImage(self, image, descript):
        """Draw the image with guides and target points."""
        self.pixmap = QPixmap(image)
        painter = QPainter(self.pixmap)
        try:
            self.drawGuides(painter)
            self.drawTargetPoints(painter)

            #file_name = self.pathLineEdit.text().split('/')[-1]  # 이미지 파일명 추출
            painter.setPen(QPen(Qt.black))
            painter.setFont(QFont("Arial", 20))
            rect = QRectF(20, self.pixmap.height() - 40, self.pixmap.width() - 30, 30)
            #painter.fillRect(rect, QColor(255, 255, 255, 200))
            painter.drawText(rect, Qt.AlignRight, descript)


        except KeyError as e:
            print(f"KeyError: {e} - Image key not found in targetPoints.")
        except Exception as e:
            print(f"Unexpected error: {e}")
        finally:
            painter.end()
    
        # Scale the pixmap once, based on width and height
        self.pixmap = self.pixmap.scaled(self.ImgWidth1, self.ImgHeight1, Qt.KeepAspectRatio)
        self.imageLabel.setPixmap(self.pixmap)
    
    def drawGuides(self, painter):
        """Draw the guide shapes and crosshair on the image."""
        pen = QPen(QColor('#ffffff'), 5)
        pen2 = QPen(QColor('#ff0000'), 5)
        painter.setPen(pen)
    
        Xoffset, Yoffset = self.calculateOffsets()
        radius = self.guideRadius
        #center = [self.guideShift1[0] + radius + Xoffset, self.guideShift1[1] + radius + Yoffset]
        center = [self.guideShift[0]+self.xCenter.value() + radius + Xoffset, self.guideShift[1]+self.yCenter.value() + radius + Yoffset]
        cross = 30
        r = QRectF(center[0] - radius, center[1] - radius, radius * 2, radius * 2)
    
        painter.drawEllipse(r)
        painter.drawLine(center[0] - cross / 2, center[1], center[0] + cross / 2, center[1])
        painter.drawLine(center[0], center[1] - cross / 2, center[0], center[1] + cross / 2)
        painter.setPen(pen2)
        painter.drawPoint(center[0], center[1])
    
    def drawTargetPoints(self, painter):
        """Draw the target points based on the image key."""
        plate_id = self.currentPlate.text().strip()
        well_id = self.currentWell.text().strip()
        imgKey   = f"{plate_id}_{well_id}"
        pens = {
            'chem': (QPen(QColor('#01567f'), 10), QPen(QColor('#4cc4ff'), 3)),
            'cryo': (QPen(QColor('#744080'), 10), QPen(QColor('#f5cdff'), 3)),
            'default': (QPen(QColor('#017f7b'), 10), QPen(QColor('#4cfff9'), 3))
        }
    
        scaleFactor = self.imgScale.value() / 100
        for points in self.targetPoints.get(plate_id, {}).get(well_id, []):
            orgX = points['x_pixel'] 
            orgY = points['y_pixel']
            #orgX = points['x_pixel'] + (self.guideShift1[0] - self.guideShift0[0])
            #orgY = points['y_pixel'] + (self.guideShift1[1] - self.guideShift0[1])
            dia = 30 * 2 / scaleFactor
        
            # 펜 색상 설정 (type 값 사용)
            pen1, pen2 = pens.get(points['type'].split('_')[0], pens['default'])
            painter.setPen(pen1)
            painter.drawPoint(orgX, orgY)
            painter.setPen(pen2)
            painter.drawEllipse(QRectF(orgX - dia / 2, orgY - dia / 2, dia, dia))



    def setTarget(self):
        groupbox = QGroupBox()
        #widget = QWidget()
        grid = QGridLayout()

        self.divisions.setValue(2)
        self.targetVol.setSingleStep(2.5)
        self.targetVol.setDecimals(1)
        self.targetVol.setSuffix('nl')

        grid.addWidget(QLabel("Targets/Well"), 0,0)
        grid.addWidget(self.divisions,        0,1)
        grid.addWidget(QLabel("TargetVolume"), 1,0)
        grid.addWidget(self.targetVol,         1,1)
        
        #groupbox.setMaximumHeight(250)
        #groupbox.setMaximumWidth(250)
        groupbox.setLayout(grid)

        return groupbox

        return 0

    def imgNavigator(self):
        groupbox = QGroupBox()
        widget = QWidget()
        grid = QGridLayout()


        self.currentWell.returnPressed.connect(self.moveToWell)
        self.currentPlate.returnPressed.connect(self.moveToPlate)

        buttonWidget = QWidget()
        buttonbox = QHBoxLayout()
        homeButton = QPushButton()
        prevButton = QPushButton()
        nextButton = QPushButton()
        lastButton = QPushButton()
        #undoButton = QPushButton()
        #self.undoButton.setCheckable(True)

        homeButton.clicked.connect(self.moveFirstWell)
        prevButton.clicked.connect(self.movePrevWell)
        nextButton.clicked.connect(self.moveNextWell)
        lastButton.clicked.connect(self.moveLastWell)
        #self.undoButton.clicked.connect(self.eraserModeOn)

        homeButton.setIcon(QIcon('{0}/{1}_first.png'.format(PATHS['icons'],self.iconColor)))
        prevButton.setIcon(QIcon('{0}/{1}_prev.png'.format(PATHS['icons'],self.iconColor)))
        nextButton.setIcon(QIcon('{0}/{1}_next.png'.format(PATHS['icons'],self.iconColor)))
        lastButton.setIcon(QIcon('{0}/{1}_end.png'.format(PATHS['icons'],self.iconColor)))
        self.undoButton.setIcon(QIcon('{0}/{1}_eraser.png'.format(PATHS['icons'],self.iconColor)))
        self.undoButton.setCheckable(True)
        #tapeButton.setIcon(QIcon('{0}/{1}_ruler.png'.format(PATHS['icons'],self.iconColor)))

        buttonbox.addWidget(homeButton)
        buttonbox.addWidget(prevButton)
        buttonbox.addWidget(nextButton)
        buttonbox.addWidget(lastButton)
        buttonbox.addWidget(self.undoButton)
        buttonWidget.setLayout(buttonbox)
        buttonWidget.setMinimumHeight(37)
        saveButton = QPushButton("Targets in Current plate")
        saveButton.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_DialogSaveButton')))
        saveButton.clicked.connect(self.saveFreeTarget)

        grid.addWidget(QLabel("Current Plate"),  0,0,1,2)
        grid.addWidget(self.currentPlate,        0,2,1,2)
        grid.addWidget(QLabel("Current Well"),   1,0,1,2)
        grid.addWidget(self.currentWell,         1,2,1,2)
        grid.addWidget(QLabel("Target/Well"),    2,0,1,2)
        grid.addWidget(self.divisions,           2,2,1,2)
        #grid.addWidget(QLabel("Selected Well"),  3,1,1,2)
        #grid.addWidget(self.currSelect_view,     3,3,1,1)
        grid.addWidget(buttonWidget,             3,0,1,4)
        #grid.addWidget(saveButton,               4,0,1,2)
        grid.setColumnStretch(0,3)
        grid.setColumnStretch(1,0)
        grid.setColumnStretch(2,0)

        #PyQt5.QtCore.QSize(800, 669)
        #groupbox.setMinimumWidth(self.OrgImage[0]*self.imgScale.value()/100)
        #groupbox.setMaximumWidth(self.OrgImgWidth*self.imgScale.value()/100)
        #groupbox.setMinimumHeight(220)
        groupbox.setMaximumHeight(250)
        groupbox.setMaximumWidth(250)
        groupbox.setLayout(grid)

        return groupbox



    def moveToPlate(self):
        wannaGo = str(self.currentPlate.text().strip())
        plates = self.plates_info.keys()
        
        if wannaGo in plates:
            print(f'[Load Plate] pID{wannaGo} selected')
            self.setCurrentPlate()
        else:
            message = f"pID{wannaGo} is not Loaded"
            react = QMessageBox.warning(self, "Check Plate ID", message, QMessageBox.Yes)
            if react == QMessageBox.Yes:
                self.currentPlate.setText(self.lastPlate)



    def moveToWell(self):
        switch = 0
        plate_id = self.currentPlate.text().strip()
        wannaGo = self.currentWell.text().strip()
        wells = self.plates_info[plate_id]['info'].keys()
        
        for i, well in enumerate(wells):
            if well.lower() == wannaGo.lower():
                print(f"Jump to {i+1}:{wannaGo}")
                self.currWellNo = i+1
                switch = 1
                self.setCurrentWell()
            else: pass
        if switch == 0:
            message = "{1} not found in pID{0}".format( self.currentPlate.text(), self.currentWell.text() )
            react = QMessageBox.warning(self, "Check Well ID", message , QMessageBox.Yes)
            if react == QMessageBox.Yes:
                #Set self.currentWell.text() as previous one
                self.currentWell.setText(self.lastWell)
        return 0



    def movePrevWell(self):
        pID = self.currentPlate.text()
        lastWellNo = len(self.plates_info[pID]['info'])
        if pID and self.currWellNo > 1:  # 현재 웰 번호가 1보다 클 때만 이동
            wells = sorted(self.plates_info[pID]['info'].keys())
            while self.currWellNo > 1:
                self.currWellNo -= 1
                if self.checkImgPath():  # 이미지가 있는 웰로 이동
                    print(f"{self.currWellNo+1}:{wells[self.currWellNo]} >> {self.currWellNo}:{wells[self.currWellNo-1]} / {lastWellNo}")
                    self.setCurrentWell()
                    break
            else:
                print('[MoveToWell] No more wells with images in this plate')
        return 0
    
    def moveNextWell(self):
        pID = self.currentPlate.text() 
        lastWellNo = len(self.plates_info[pID]['info'])
        if pID and self.currWellNo < lastWellNo:
            wells = sorted(self.plates_info[pID]['info'].keys())
            while self.currWellNo < lastWellNo:
                self.currWellNo += 1
                if self.checkImgPath():  # 이미지가 있는 웰로 이동
                    print(f"{self.currWellNo-1}:{wells[self.currWellNo-2]} >> {self.currWellNo}:{wells[self.currWellNo-1]} / {lastWellNo}")
                    self.setCurrentWell()
                    break
            else:
                print('[MoveToWell] No more wells with images in this plate')
        return 0


    def moveFirstWell(self):
        pID = self.currentPlate.text()
        if pID != 'none' and pID != '':
            # 현재 웰이 첫 번째 웰이 아닐 때만 이동
            if self.currWellNo > 1:
                self.currWellNo = 1
                self.setCurrentWell()
                # 첫 번째 웰에 imgPath가 없으면 다음 웰로 이동
                while not self.checkImgPath() and self.currWellNo < len(self.plates_info[pID]['info']):
                    self.moveNextWell()
        return 0
    
    def moveLastWell(self):
        pID = self.currentPlate.text()
        lastWellNo = len(self.plates_info[pID]['info'])
        if pID != 'none' and pID != '':
            # 현재 웰이 마지막 웰이 아닐 때만 이동
            if self.currWellNo < lastWellNo:
                self.currWellNo = lastWellNo
                self.setCurrentWell()
                # 마지막 웰에 imgPath가 없으면 이전 웰로 이동
                while not self.checkImgPath() and self.currWellNo > 1:
                    self.movePrevWell()
        return 0


    def checkImgPath(self):
        pID = self.currentPlate.text()
        well_id = sorted(self.plates_info[pID]['info'].keys())[self.currWellNo - 1]
        well_info = self.plates_info[pID]['info'].get(well_id)
        return well_info and well_info.get('imgPath')



    def removeLastTarget(self):
        return 0


    def saveFreeTarget(self):
        return 0


    def setChemicals(self):
        groupbox = QGroupBox("Fragment Library")
        #widget = QWidget()
        grid = QGridLayout()

        grid.addWidget(QLabel("Targets/Well"), 0,0)
        grid.addWidget(self.divisions,        0,1)
        grid.addWidget(QLabel("TargetVolume"), 1,0)
        grid.addWidget(self.targetVol,         1,1)

        #groupbox.setMaximumHeight(250)
        #groupbox.setMaximumWidth(250)
        groupbox.setLayout(grid)

        return groupbox




    def evaluePlans(self):
        groupbox = QGroupBox("Evaluate Plan")
        widget = QWidget()
        grid = QGridLayout()


        treat_mode_box = QComboBox()
        treat_mode_box.addItem('Individual')
        #treat_mode_box.addItem('Cryopro Only')
        treat_mode_box.addItem('Combined')


        add1_type_box = QComboBox()
        add1_type_box.addItem('DMSO')
        add1_type_box.addItem('EG')
        add1_type_box.addItem('Cryopro')
        add1_type_box.addItem('Not Chosen')
        add1_type_box.addItem('Directly')
        add1_type_diy = QLineEdit()
        add1_type_box.setCurrentText('DMSO')
       

        add2_type_box = QComboBox()
        add2_type_box.addItem('DMSO')
        add2_type_box.addItem('EG')
        add2_type_box.addItem('Cryopro')
        add2_type_box.addItem('Not Chosen')
        add2_type_box.addItem('Directly')
        add2_type_diy = QLineEdit()
        add2_type_box.setCurrentText('Cryopro')

        incubation_time_line  = QLineEdit("0h, 1h, 2h, 6h")
        add1_vol_line      = FloatLineEdit("5, 15, 30")
        add2_vol_line      = FloatLineEdit("80, 100")
        replica_num_box       = QSpinBox()
        replica_num_box.setValue(3)

        self.pilot_tableview.setModel(self.pilot_tableview_model)
        self.pilot_tableview.setSelectionBehavior(QAbstractItemView.SelectRows)  # 행 단위 선택
        self.pilot_tableview.setSelectionMode(QAbstractItemView.SingleSelection)  # 한 번에 하나의 행만 선택



        add = QPushButton('Add')
        add.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        save = QPushButton('Save')
        save.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        reset = QPushButton('Reset')
        reset.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        #print( type(solvents), type(incubation_time_line), type(solvent_vol_line), type(cryopro_vol_line), type(replica_num_box))
        

        add.clicked.connect (lambda: self.add_pilot_list( treat_mode_box.currentText(), \
                                                          incubation_time_line, \
                                                          add1_type_box, add1_type_diy, add1_vol_line, \
                                                          add2_type_box, add2_type_diy, add2_vol_line, \
                                                          replica_num_box ) )
        reset.clicked.connect ( lambda: self.clear_pilot_list() )
        #add.clicked.connect(lambda: self.makeEvalueConditions(solvents, incubation_time_line, solvent_vol_line, cryopro_vol_line, replica_num_box))
        #undo.clicked.connect(lambda: self.makeEvalueConditions(solvents, incubation_time_line, solvent_vol_line, cryopro_vol_line, replica_num_box))
        #reset.clicked.connect(lambda: self.makeEvalueConditions(solvents, incubation_time_line, solvent_vol_line, cryopro_vol_line, replica_num_box))

        btnSaveTargets = QPushButton("Targets Only")
        btnSaveTargets.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_DialogSaveButton')))
        btnSaveTargets.clicked.connect(self.saveTargetList)
        btnSaveWorksheet = QPushButton("Worksheets")
        btnSaveWorksheet.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_DialogSaveButton')))
        btnSaveWorksheet.clicked.connect(self.makeEvalWorksheet)
        self.btnEvalueToMxlive = QPushButton("Upload To MxLive")
        self.btnEvalueToMxlive.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_ArrowUp')))
        self.btnEvalueToMxlive.clicked.connect(self.deliverToMxlive)


        for i in range(0,10):
            grid.setColumnStretch(i, 1)

        #grid.addWidget(QLabel("Protein"),         0,0,1,4)
        #grid.addWidget(self.crystname,            0,4,1,6)
        grid.addWidget(QLabel("Treat Mode"),       0,0,1,4)
        grid.addWidget(treat_mode_box,             0,4,1,6)    
        grid.addWidget(QLabel("Additive1"),        1,0,1,4)
        grid.addWidget(add1_type_box,              1,4,1,3)
        grid.addWidget(add1_type_diy,              1,7,1,3)
        grid.addWidget(QLabel("Add1 Vol (nl)"),    2,0,1,4)
        grid.addWidget(add1_vol_line,              2,4,1,6)
        grid.addWidget(QLabel("Additive2"),        3,0,1,4)
        grid.addWidget(add2_type_box,              3,4,1,3)
        grid.addWidget(add2_type_diy,              3,7,1,3)
        grid.addWidget(QLabel("Add2 Vol (nl)"),    4,0,1,4)
        grid.addWidget(add2_vol_line,              4,4,1,6)
        grid.addWidget(QLabel("Incubation"),       5,0,1,4)
        grid.addWidget(incubation_time_line,       5,4,1,6)
        
        grid.addWidget(QLabel("Replication"),      6,0,1,4)
        grid.addWidget(replica_num_box,            6,4,1,6)
        #grid.addWidget(QLabel("Target/Well"),     6,0,1,4)
        #grid.addWidget(self.divisions,            6,4,1,6)
        grid.addWidget(reset,                      7,0,1,5)
        grid.addWidget(add,                        7,5,1,5)
        #grid.addWidget(save,                     7,6,1,3)
        grid.addWidget(btnSaveTargets,            8,0,1,5)
        grid.addWidget(btnSaveWorksheet,          8,5,1,5)
        grid.addWidget(self.btnEvalueToMxlive,        9,0,1,10)

        grid.addWidget(self.pilot_tableview,       10, 0,5,10)

        groupbox.setLayout(grid)

        return groupbox




    def add_pilot_list(self, mode, incubation_time_line, add1_type_box, add1_type_diy, add1_vol_line, add2_type_box, add2_type_diy, add2_vol_line, replica_num_box):
        times = incubation_time_line.text().replace(' ', '').split(',')
        times = list(filter(bool, times)) 
        replica = replica_num_box.value()
        add1_type = add1_type_box.currentText()
        add2_type = add2_type_box.currentText()
        #add1_vol = add1_vol_line.text().replace(' ', '').split(',')
        #add2_vol = add2_vol_line.text().replace(' ', '').split(',')
        try:
            add1_vol = [float(v) for v in add1_vol_line.text().replace(' ', '').split(',')]
            add2_vol = [float(v) for v in add2_vol_line.text().replace(' ', '').split(',')]
        except ValueError:
            print("Add1 Vol(nl) 또는 Add2 Vol(nl)에서 입력한 값 중 정수로 변환할 수 없는 값이 있습니다.")
            return  # 함수 종료


        add1_vol = list(filter(bool, add1_vol))
        add2_vol = list(filter(bool, add2_vol))        

        if add1_type == 'Directly': add1_type = add1_type_diy.text()
        if add2_type == 'Directly': add2_type = add2_type_diy.text()
        if add1_type == 'Not Chosen':
            add1_vol = []
            add1_type = ''
        if add2_type == 'Not Chosen':
            add2_vol = []
            add2_type = ''
    
        print(add1_type, add1_vol, add2_type, add2_vol)
    
        # 조건을 저장할 리스트
        sample_conditions = []
        
        # 각 replica 별로 색상을 저장할 딕셔너리
        color_dict = {}
    
        # 데이터 입력 로직
        if mode == 'Individual':
            for t in times:
                if add1_type == add2_type:
                    for v in sorted(add1_vol + add2_vol):
                        print(type(v))
                        for i in range(replica):
                            condition = {'time': t, add1_type: v}
                            sample_conditions.append(condition)
                else:
                    for v1 in add1_vol:
                        for i in range(replica):
                            condition = {'time': t, add1_type: v1}
                            sample_conditions.append(condition)
                    for v2 in add2_vol:
                        for i in range(replica):
                            condition = {'time': t, add2_type: v2}
                            sample_conditions.append(condition)
        elif mode == 'Combined':
            for t in times:
                for v2 in add2_vol:
                    for v1 in add1_vol:
                        for i in range(replica):
                            condition = {'time': t, add1_type: v1, add2_type: v2}
                            sample_conditions.append(condition)
    
        # 테이블 뷰에 데이터를 추가합니다.
        rows = self.pilot_tableview_model.rowCount()
    
        # 헤더 설정
        self.pilot_tableview_model.setHorizontalHeaderLabels(['Time', 'Add1', add1_type, 'Add2', add2_type, 'TargetWell'])
    
        for i, condition in enumerate(sample_conditions):
            # 같은 replica 그룹에 대해 동일한 색상을 사용
            group = i // replica
            if group not in color_dict:
                # 랜덤 RGB 색상 생성
                random_color = QColor(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
                color_dict[group] = random_color
    
            color = color_dict[group]
            if add1_type == add2_type and mode == 'Individual':
                column01 = QStandardItem(str(condition.get('time', '0H')))
                column02 = QStandardItem(str(add1_type))
                column03 = QStandardItem(str(condition.get(add1_type, '')))
                column04 = QStandardItem(str(add2_type))
                column05 = QStandardItem(str(''))
                column06 = QStandardItem(str(''))
            else:
                column01 = QStandardItem(str(condition.get('time', '0H')))
                column02 = QStandardItem(str(add1_type))
                column03 = QStandardItem(str(condition.get(add1_type, '')))
                column04 = QStandardItem(str(add2_type))
                column05 = QStandardItem(str(condition.get(add2_type, '')))
                column06 = QStandardItem(str(''))            

            # 같은 그룹의 폰트 색상을 동일하게 설정
            column01.setForeground(color)
            column02.setForeground(color)
            column03.setForeground(color)
            column04.setForeground(color)   
            column05.setForeground(color)
            #column06.setForeground(color) 

            self.pilot_tableview_model.setItem(rows + i, 0, column01)
            self.pilot_tableview_model.setItem(rows + i, 1, column02)
            self.pilot_tableview_model.setItem(rows + i, 2, column03)
            self.pilot_tableview_model.setItem(rows + i, 3, column04)
            self.pilot_tableview_model.setItem(rows + i, 4, column05)    
            #self.pilot_tableview_model.setItem(rows + i, 5, column06) 

            # 정렬 설정
            column01.setTextAlignment(Qt.AlignHCenter | Qt.AlignVCenter | Qt.AlignCenter)
            column02.setTextAlignment(Qt.AlignHCenter | Qt.AlignVCenter | Qt.AlignCenter)
            column03.setTextAlignment(Qt.AlignHCenter | Qt.AlignVCenter | Qt.AlignCenter)
            column04.setTextAlignment(Qt.AlignHCenter | Qt.AlignVCenter | Qt.AlignCenter)   
            column05.setTextAlignment(Qt.AlignHCenter | Qt.AlignVCenter | Qt.AlignCenter)
 
        # 필수적이지 않은 컬럼 숨김 
        for column_index in range(self.pilot_tableview_model.columnCount()):
            self.pilot_tableview.showColumn(column_index)
        hidden_columns = [1,3]
        #if add1_type == add2_type: hidden_columns.append(4)
        if add1_type == '': hidden_columns.append(2)
        if add2_type == '': hidden_columns.append(4)
        for column_index in hidden_columns:
            self.pilot_tableview.hideColumn(column_index)
            self.pilot_tableview.horizontalHeader().setSectionResizeMode(column_index, QHeaderView.ResizeToContents)
 
        # 테이블 크기 조정
        self.pilot_tableview.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.pilot_tableview.verticalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.pilot_tableview.resizeColumnsToContents()
        self.pilot_tableview.resizeRowsToContents()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        #self.pilot_tableview.updateGeometry()    
        # 첫 번째로 유효하지 않은 행을 선택하는 함수 호출
        self.select_first_invalid_row(replica)
    
        return sample_conditions





    def select_first_invalid_row(self, replica):
        # 테이블뷰 모델의 행 수를 가져옵니다.
        row_count = self.pilot_tableview_model.rowCount()
    
        # 4번째 컬럼의 데이터를 검사합니다.
        for row in range(row_count):
            item = self.pilot_tableview_model.item(row, 3)
            if item:
                value = item.text()
                print('!', value)
                wells = value.replace(' ','').split(',')
                print(len(wells), wells)
                # 조건을 만족하지 않는 값을 찾음 (예: 특정 문자열이 아닐 때)
                if len(wells) < replica:
                    # 조건에 맞지 않는 행을 current로 선택
                    index = self.pilot_tableview_model.index(row, 0)  # 첫 번째 컬럼 인덱스 사용
                    self.pilot_tableview.setCurrentIndex(index)
                    self.pilot_tableview.selectRow(row)
                    break
            else:
                self.pilot_tableview.selectRow(row)
                break

    def add_well_toPilot(self, pID, well):
        
        row_count = self.pilot_tableview_model.rowCount()
        # 현재 선택된 행의 인덱스를 가져옵니다.
        current_index = self.pilot_tableview.currentIndex()

        # 인덱스가 유효한지 확인 (선택된 행이 있는 경우)
        if current_index.isValid():
            # 현재 행 번호를 가져옵니다.
            current_row = current_index.row()

            # 4번째 컬럼(즉, 3번째 인덱스)에 값 추가
            value = f"{pID}_{well}"
            column04 = QStandardItem(str(value))

            # 테이블 모델에 값을 설정
            self.pilot_tableview_model.setItem(current_row, 5, column04)

            # 값이 가운데 정렬되도록 설정 (필요 시)
            column04.setTextAlignment(Qt.AlignHCenter|Qt.AlignVCenter|Qt.AlignCenter)
            
            # 다음 리스트로 이동
            if current_row < row_count: self.pilot_tableview.selectRow(current_row +1)
        else:
            print("No valid row selected.") 

        

    def clear_pilot_list(self):
        # 대화창에 커스텀 버튼 추가
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle('Clear Confirmation')
        msg_box.setText("타겟 포인트와 시료 목록을 모두 삭제하시겠습니까?")
        
        # 커스텀 버튼 추가
        list_only_button = msg_box.addButton('Clear List Only', QMessageBox.ActionRole)
        both_button = msg_box.addButton('Clear Both', QMessageBox.ActionRole)
        cancel_button = msg_box.addButton(QMessageBox.Cancel)
    
        # 대화창 실행
        msg_box.exec_()
    
        # 선택에 따른 동작 처리
        if msg_box.clickedButton() == list_only_button:
            # 목록만 지우기
            print("List cleared.")
            self.pilot_tableview_model.removeRows(0, self.pilot_tableview_model.rowCount())
            self.pilot_tableview_model.removeColumns(0, self.pilot_tableview_model.columnCount())
        elif msg_box.clickedButton() == both_button:
            # 목록과 타겟 포인트 모두 지우기
            print("List and target points cleared.")
            self.pilot_tableview_model.removeRows(0, self.pilot_tableview_model.rowCount())
            self.pilot_tableview_model.removeColumns(0, self.pilot_tableview_model.columnCount())
            self.targetPoints.clear()
            #TODO 선택된 웰의 갯수를 보여주는 부분을 모두 새로고침 할 것
        else:
            # 취소 선택 시 아무 동작도 하지 않음
            print("Operation cancelled.")
    
        return 0


    #Library list와 동일한 역할을 하는 자료를 만듦
    def makeEvalueConditions_toRemove(self, list_solvents, line_times, line_solvVol, line_cryoVol, spinbox_replica):
        sender = self.sender()
        solvents = list_solvents
        times    = line_times.text().replace(' ', '').split(',')
        solvVol  = line_solvVol.text().replace(' ', '').split(',')
        cryoVol  = line_cryoVol.text().replace(' ', '').split(',')
        replica  = spinbox_replica.value()    

       

        cryo_tags = dict(zip([f'Cryo-Conc{i+1}' for i in range(len(cryoVol))], cryoVol))
        solv_tags = {}
        for solvent in solvents:
            st = dict(zip([f'{solvent}-Conc{i+1}' for i in range(len(solvVol))], solvVol))
            solv_tags.update(st)
        #all_tags  = {**solv_tags, **cryo_tags}
        
        for time in times:
            for key, val in solv_tags.items():
                for i in range(0,replica):
                    append = f'{time}_{key}_{int(val):02}nl_xtal{(i+1):01}'
                    print(append)
         
        #최종 완료 버튼을 만들고, 이 버튼을 클릭했을 때 solvent를 제공할 source 주소를 받는다
   
        
        """ 
        ### Variable Declaration
        appendixes = []
        doSolvent = [] #for echo worksheet [harvest time, solvent type, total volume to be added in a drop]
        doCryopro = [] #for echo worksheet [harvest time, 'CRYO'      , total volume to be added in a drop]
        durationVolume = {}            #Variable for the required volume for each solutions
        for item in solvents+['CRYO']: #Assign key value: Solution Types to be used
            durationVolume[item] = 0
        ## Create appendixes to be used in MXDC Sample Name
        for t in times:
            for s in solvents:
                for cv in cryoVol:
                    for sv in solvVol:
                        #Case1: timely solvent duration test
                        if cryoVol == ['']:
                        #if len(cryoVol) == 0:
                            appendix = "{0}-{1}{2}nl".format(t, s, sv)
                            sDo = [t, s, sv]
                            cDo = False
                            print(appendix, sDo, cDo)
                        #Case2: timely cryopro duration test
                        elif solvents == [''] or solvVol == ['']:
                        #elif len(solvents)==0 or len(solvVol) == 0:
                            appendix = "{0}-Cryo{1}nl".format(t,cv)
                            sDo = False
                            cDo = [t,'CRYO',cv]
                        #Case3: solvent and cryopro duration test at one-time
                        elif times == ['']:
                        #elif len(times) == 0:
                            appendix = "Cryo{2}nl-{0}{1}nl".format(s, sv, cv)
                            sDo = [0, s, sv]
                            cDo = [0,'CRYO', cv]
                        #Case4 timely solvent and cryopro duration test
                        else:
                            appendix = "{0}-Cryo{3}nl-{1}{2}nl".format(t, s, sv, cv)
                            sDo = [t, s, sv]
                            cDo = [t,'CRYO', cv]
                        #By the Replication number
                        #write appendixes to be used in MXDC sample name
                        #write doSolvent and doCryopro to be used to write a echo worksheer
                        #calculate the volume of solutions users need to prepare
                        if replica == 1:
                            print(appendix)
                            appendixes.append(appendix)
                            if sDo and solvents != [''] and solvVol != ['']:
                                doSolvent.append(sDo)
                                durationVolume[s] += float(sv)
                            if cDo and cryoVol != ['']:
                                doCryopro.append(cDo)
                                durationVolume['CRYO'] += float(cv)
                        else:
                            for i in range(int(self.replica.value())):
                                appendixes.append(appendix+'-{0}'.format(i+1))
                                if sDo and solvents != [''] and solvVol != ['']:
                                    doSolvent.append(sDo)
                                    durationVolume[s] += float(sv)
                                if cDo and cryoVol != ['']:
                                    doCryopro.append(cDo)
                                    durationVolume['CRYO'] += float(cv)
        print(len(doSolvent))
        """
        conditions = []
        return 0

    def screenPlans(self):
        groupbox = QGroupBox("Screen Plan")
        widget = QWidget()
        grid = QGridLayout()


        ###Variables for [screenPlans]
        library_combobox = QComboBox()
        targetVol = QDoubleSpinBox()
        regionLib = QLineEdit()


        libList = ['**none**']
        for item in os.listdir(PATHS['library']):
            print('[Libraries]', item)
            if item.endswith('.csv'):
                #filename = '{0}/{1}'.format(PATHS['library'], item)
                libname  = item.replace('.csv', '')
                libList.append(libname)
            else: pass
        for item in sorted(libList):
            library_combobox.addItem(item)

        btnSaveTargets = QPushButton("Targets Only")
        btnSaveTargets.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_DialogSaveButton')))
        btnSaveTargets.clicked.connect(self.saveTargetList)
        btnSaveWorksheet = QPushButton("Worksheets")
        btnSaveWorksheet.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_DialogSaveButton')))
        btnSaveWorksheet.clicked.connect(self.makeChemWorksheet)
        self.btnScreenToMxlive = QPushButton("Upload To MxLive")
        self.btnScreenToMxlive.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_ArrowUp')))
        self.btnScreenToMxlive.clicked.connect(self.deliverToMxlive)

        #self.divisions.setValue(2)
        targetVol.setRange(2.5, 300)
        targetVol.setSingleStep(2.5)
        targetVol.setDecimals(1)
        targetVol.setSuffix('nl')
        library_combobox.setCurrentIndex(0)
        library_combobox.currentIndexChanged.connect(lambda: self.selectLibrary(library_combobox.currentText()))

        #grid.addWidget(QLabel("Protein"),         0,0,1,4)
        #grid.addWidget(self.crystname,            0,4,1,6)
        #grid.addWidget(QLabel("Target/Well"),     1,0,1,4)
        #grid.addWidget(self.divisions,            1,4,1,6)
        grid.addWidget(QLabel("Library"),         2,0,1,4)
        grid.addWidget(library_combobox,          2,4,1,6)
        grid.addWidget(QLabel("Region"),          3,0,1,4)
        grid.addWidget(self.regionLib,            3,4,1,6)
        grid.addWidget(QLabel("Vol/Well"),        4,0,1,4)
        grid.addWidget(targetVol,            4,4,1,6)

        grid.addWidget(btnSaveTargets,            5,0,1,5)
        grid.addWidget(btnSaveWorksheet,          5,5,1,5)
        grid.addWidget(self.btnScreenToMxlive,         6,0,1,10)

        groupbox.setLayout(grid)

        return groupbox




    def cryoPlans(self):
        groupbox = QGroupBox("Cryo Plan")
        grid = QGridLayout()


        sites = ['To Selected Points', 'To 4 Sides', 'To 6 Sides', 'Assign Manually (yet)']
        for item in sites:
            self.cryoStrategy.addItem(item)
        self.cryoSites.setRange(0,10)
        self.cryoVolume.setRange(0, 1000)
        self.cryoVolume.setSingleStep(0)
        self.cryoVolume.setDecimals(1)
        self.cryoVolume.setSuffix('nl')

        btnStartManualSelect = QPushButton("Start To Targeting")
        btnClearCryoPoints   = QPushButton("Cancel Targeting")
        btnSaveCryoWorksheet = QPushButton("Save")
        btnSendCryoWorksheet = QPushButton("SendToECHO")
        btnSaveCryoWorksheet.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_DialogSaveButton')))
        btnSendCryoWorksheet.setIcon(self.style().standardIcon(getattr(QStyle, 'SP_ArrowRight')))

        self.cryoStrategy.currentIndexChanged.connect(self.selectCryoPlans)

        grid.addWidget(QLabel("Transfer"),        0,0,1,4)
        grid.addWidget(self.cryoStrategy,         0,4,1,6) 
        grid.addWidget(QLabel("Volume/Well"),     1,0,1,4)
        grid.addWidget(self.cryoVolume,           1,4,1,6)
        grid.addWidget(QLabel("Source Well"),     2,0,1,4)
        grid.addWidget(QLineEdit(),               2,4,1,6)
        #grid.addWidget(QLabel("Targets/Well"),    3,0,1,4)
        #grid.addWidget(self.cryoSites,            3,4,1,6)
        #grid.addWidget(btnStartManualSelect,      4,0,1,10)
        #grid.addWidget(btnClearCryoPoints,        5,0,1,10)
        grid.addWidget(btnSaveCryoWorksheet,      6,0,1,5)
        grid.addWidget(btnSendCryoWorksheet,      6,5,1,5)
        groupbox.setLayout(grid)

        return groupbox

    def selectCryoPlans(self):
        strategy = self.cryoStrategy.currentText()
        ### Final Result Form (Constant for this DeF)
        echoHeader = ['Type', \
                      'Source plate name', 'Source well', 'Transfer Volume', \
                      'Destination plate name', 'Targeting Well(forView)','Destination Well',\
                      'Destination Well X offset', 'Destination Well Y offset']
        echoCryoList = [echoHeader]
        cryoVolume = self.cryoVolume.value()
        plateType = self.PlateType.currentText()
        srcPlate = 'SourcePlate'
        srcType  = 'CryoProtectant'
        srcWell  = 'A00'
        #self.targetPoints >> echoCryoList
        if strategy == 'To Selected Points':
            print('yet')
            for key, val in self.targetPoints.items():
                ctimes = float(cryoVolume) / 2.5 # 2.5ul : Minimum transfer value
                cquota = ctimes // len(val)
                cremain = ctimes % len(val)
                destPlate = key.split('_')[0]
                destWell1 = key.split('_')[1]
                destWell2 = adpt.transPlate(plateType, destWell1)
                for i, item in enumerate(val):
                    if i+1 == len(val):
                        ctransVol = (cquota+cremain) * 2.5
                    else:
                        ctransVol = cquota * 2.5
                    destYoffset = item[2]
                    if destWell1.endswith('d'): destXoffset = item[1] -700
                    else                      : destXoffset = item[1]
                    echoCryoList.append( ['CryoProtectant', srcPlate, srcWell, ctransVol, \
                                           destPlate, destWell1, destWell2, destXoffset, destYoffset] )

            for item in echoCryoList:
                print('CryoTEST', item) 
        elif strategy == 'To 4 Sides':
            print('yet')
        elif strategy == 'To 6 Sides':
            print('yet')
        elif strategy == 'Assign Manually':
            print('yet')
            print(self.targetPoints)
            selectedImgKeys = []
            for key,val in self.targetPoints.items():
                selectedImgKeys.append(key)
            print('selected', selectedImgKeys)
            firstPlate = selectedImgKeys[0].split('_')[0]
            firstWell  = selectedImgKeys[0].split('_')[1]
            print(firstPlate, firstWell)
            self.currentPlate.setText(firstPlate)
            self.moveToPlate()
            self.currentWell.setText(firstWell)
            self.moveToWell()
           
        for item in echoCryoList:
            print('CryoTEST', item)

        else: pass
        return 0
    def takeCryoPoints(self):
        #Flag of the cryo targeting
        #Move to First Selected Well

        return 0


    def selectLibrary(self, library):
        libfile = "{0}/{1}.csv".format(PATHS['library'],library)
        #Reset the objective Library information 
        self.libraryInfo.clear()
        with open(libfile, 'r') as f:
            contents = csv.reader(f)
            for line in contents:
                self.libraryInfo.append(line)
                print(line)
        #Analyse header
        #header = ['Vendor', 'Library', 'No', 'ID', 'Formula', 'MW', 'Smile', 'Conc_mM', 'Solvent', 'Plate_ID', 'Plate_well']
        
        self.setRegion()
        return 0
    def setRegion(self):
        last = len(self.libraryInfo) -1
        first = 1
        self.regionLib.setText('{0}-{1}'.format(first, last))
        return 0

    def getRegion(self):
        region0  = self.regionLib.text()
        regions = region0.replace(' ','').replace('~', '-').split(',')

        well_numbers = set()

        for region in regions:
            try:
                if '-' in region:
                    start, end = map(int, region.split('-'))
                    if start > end:
                        raise ValueError(f"Wrong Range: {region}")
                    well_numbers.update(range(start, end+1))
                elif region.isdigit():
                    well_numbers.update(int(region))
                else:
                    raise ValueError(f"Wrong Range: {region}")
            except ValueError as e:
                print(f"Input Error: {e}")
                print(traceback.format_exc())
        return sorted(well_numbers)

    def moveNextTarget(self):
        return 0
    def saveTargetList(self):
        defaultname = "target.csv"
        filesave = QFileDialog.getSaveFileName(self,"Save free target (csv)",\
                                               #PATHS['userhome']+'/Documents/'+defaultname, "CSV Files (*.csv)")
                                               USERHOME_PATH +'/Documents/'+defaultname, "CSV Files (*.csv)")
        if filesave[0] != '':
            if filesave[0].endswith('.csv'):
                filename = filesave[0]
            else:
                filename = filesave[0] + '.csv'
            targetList = self.makeTargetList() 
            with open(filename, 'w') as f:
                csvWriter = csv.writer(f)
                for line in targetList:
                    csvWriter.writerow(line)
            
        return 0
    def makeTargetList(self):
        #print(self.targetPoints)
        header = ['Destination plate name', 'Targeting Well(forView)','Destination Well',\
                  'Destination Well X offset', 'Destination Well Y offset']
        plateType = self.PlateType.currentText()
        targetList = [header]
        for key,val in self.targetPoints.items():
            #print(key, val)
            for item in val:
                destPlate = key.split('_')[0]
                destWell1 = key.split('_')[1]
                destWell2 = adpt.transPlate(plateType,'384', destWell1) 
                destYoffset = item[2]
                if destWell1.endswith('d'): destXoffset = item[1] -700
                else                      : destXoffset = item[1]
                targetList.append( [destPlate, destWell1, destWell2, destXoffset, destYoffset] )
        for item in targetList:
            print('[TargetOnly]',item)
        return targetList



    def scanEvalMethod(self):
        ### Import User Input : from QLineEdit to List
        solvents = self.solvents.text().replace(' ', '').split(',')
        times    = self.incuTime.text().replace(' ', '').split(',')
        solvVol  = self.solventVol.text().replace(' ', '').split(',')
        cryoVol  = self.cryoVol.text().replace(' ', '').split(',')
        replica  = self.replica.value()
        #if solvents == ['']: solvents = []
        #if times    == ['']: times    = []
        #if solvVol  == ['']: solvVol  = []
        #if cryoVol  == ['']: cryoVol  = []
        ### Remove after stablized
        print('[EvaluateWorksheet] {0} Solvents:{1}'.format(len(solvents), solvents))
        print('[EvaluateWorksheet] {0} time harvest:{1}'.format(len(times), times))
        print('[EvaluateWorksheet] {0} Solvent Volumes:{1} nl'.format(len(solvVol), solvVol))
        print('[EvaluateWorksheet] {0} Cryopro Volumes:{1} nl'.format(len(cryoVol),cryoVol))

        ### Variable Declaration
        appendixes = []
        doSolvent = [] #for echo worksheet [harvest time, solvent type, total volume to be added in a drop]
        doCryopro = [] #for echo worksheet [harvest time, 'CRYO'      , total volume to be added in a drop]
        durationVolume = {}            #Variable for the required volume for each solutions
        for item in solvents+['CRYO']: #Assign key value: Solution Types to be used
            durationVolume[item] = 0
        ## Create appendixes to be used in MXDC Sample Name
        for t in times:
            for s in solvents:
                for cv in cryoVol:
                    for sv in solvVol:
                        #Case1: timely solvent duration test
                        if cryoVol == ['']:
                        #if len(cryoVol) == 0:
                            appendix = "{0}-{1}{2}nl".format(t, s, sv)
                            sDo = [t, s, sv]
                            cDo = False
                            print(appendix, sDo, cDo)
                        #Case2: timely cryopro duration test
                        elif solvents == [''] or solvVol == ['']:
                        #elif len(solvents)==0 or len(solvVol) == 0:
                            appendix = "{0}-Cryo{1}nl".format(t,cv)
                            sDo = False
                            cDo = [t,'CRYO',cv]
                        #Case3: solvent and cryopro duration test at one-time
                        elif times == ['']:
                        #elif len(times) == 0:
                            appendix = "Cryo{2}nl-{0}{1}nl".format(s, sv, cv)
                            sDo = [0, s, sv]
                            cDo = [0,'CRYO', cv]
                        #Case4 timely solvent and cryopro duration test
                        else:
                            appendix = "{0}-Cryo{3}nl-{1}{2}nl".format(t, s, sv, cv)
                            sDo = [t, s, sv]
                            cDo = [t,'CRYO', cv]
                        #By the Replication number
                        #write appendixes to be used in MXDC sample name
                        #write doSolvent and doCryopro to be used to write a echo worksheer
                        #calculate the volume of solutions users need to prepare
                        if replica == 1:
                            print(appendix)
                            appendixes.append(appendix)  
                            if sDo and solvents != [''] and solvVol != ['']:
                                doSolvent.append(sDo)
                                durationVolume[s] += float(sv)
                            if cDo and cryoVol != ['']:
                                doCryopro.append(cDo)
                                durationVolume['CRYO'] += float(cv)
                        else:
                            for i in range(int(self.replica.value())):
                                appendixes.append(appendix+'-{0}'.format(i+1))            
                                if sDo and solvents != [''] and solvVol != ['']:
                                    doSolvent.append(sDo)
                                    durationVolume[s] += float(sv)
                                if cDo and cryoVol != ['']: 
                                    doCryopro.append(cDo)
                                    durationVolume['CRYO'] += float(cv)

                                #if sDo: doSolvent.append(sDo)
                                #if cDo: doCryopro.append(cDo)
        ### Remove after stablized
        for i,item in enumerate(appendixes):
            try:
                if len(doSolvent) == 0:
                    print('[EvaluateWorksheet] sample name: {0} / toDo:{1}'.format(item, doCryopro[i]))
                elif len(doCryopro) == 0:
                    print('[EvaluateWorksheet] sample name: {0} / toDo:{1}'.format(item, doSolvent[i]))
                else:
                    print('[EvaluateWorksheet] sample name: {0} / toDo:{1}/{2}'.format(item, doSolvent[i], doCryopro[i]))
            except IndexError as e:
                print('[EvaluateWorksheer] please check your Evaluation plan:{0}'.format(e))
        
        ### Method duration user needs to prepare
        #self.requestMessage.append('This method requires {0} drops'.format(len(appendixes)))
        #for key, val in durationVolume.items():
        #    self.requestMessage.append('Requires {0}ul {1}'.format(val,key))
        return doSolvent, doCryopro, appendixes, durationVolume




    
    def classify_entries(self,pilot_list):
        """Classify entries into a dictionary based on 'Add1' and 'Add2' types."""
        classified_lists = {}
        for entry in pilot_list:
            add1_type = entry.get('Add1', '')
            add2_type = entry.get('Add2', '')
            add1_vol = entry.get(add1_type, '')
            add2_vol = entry.get(add2_type, '')
    
            for add_type, add_vol in [(add1_type, add1_vol), (add2_type, add2_vol)]:
                if add_type and add_vol:
                    if add_type not in classified_lists:
                        classified_lists[add_type] = []
                    classified_lists[add_type].append(entry)
        
        return classified_lists
    
    
    def calculate_shots(self, transfer_vol, num_points):
        """Calculate shots per point and remaining shots for the last point."""
        shots = int(float(transfer_vol) / 2.5)
        remain = float(transfer_vol) % 2.5
        if remain >= 1.25:
            shots += 1
        shots_per_point = shots // num_points
        shots_for_last_point = shots_per_point + (shots % num_points)
        
        return shots_per_point, shots_for_last_point
    
    
    def generate_shifter_row(self,plateType, dest_plate, dest_well):
        """Generate a row entry for the Shifter worklist."""
        dest_well_row = dest_well[0]
        dest_well_col = str(int(dest_well[1:-1]))
        dest_well_sub = dest_well[-1]
        return [plateType, dest_plate, 'AM', dest_well_row, dest_well_col, dest_well_sub]
    
    
    def generate_echo_rows(self, harvest_time, add_type, shots_per_point, points, dest_well, dest_well_sub):
        """Generate rows for the Echo worklist."""
        echo_rows = []
        for i, p in enumerate(points):
            x, y = p['x_um'], p['y_um']
            if dest_well_sub == 'd':  # Adjust X for subwell 'd'
                x -= 700
            #transfer_volume = shots_for_last_point * 2.5 if i == 0 else shots_per_point * 2.5
            transfer_volume = shots_per_point[i]
            echo_row = [
                harvest_time, add_type, 'source01', add_type, transfer_volume, 'crystal', 
                dest_well, dest_well, x, y
            ]
            echo_rows.append(echo_row)
        return echo_rows

    
    #def distribute_evenly(self, total, parts):
    #    base = total // parts
    #    remainder = total % parts
    #    return [base] * (parts - 1) + [base + remainder]


    def distribute_evenly(self, total, parts):
        # total을 2.5의 배수로 반올림합니다.
        total = round(float(total) / 2.5) * 2.5
        
        # 각 part의 기본 할당량을 2.5의 배수로 계산
        base = math.floor(total / parts / 2.5) * 2.5
        remainder = total - (base * parts)
        
        # 기본 할당량과 남은 remainder를 분배해 2.5의 배수가 되도록 결과 생성
        result = [base] * parts
        for i in range(int(remainder // 2.5)):
            result[i] += 2.5
        
        return result


    def distribute_cumulative(self, total, parts):
        base = total // parts
        remainder = total % parts
        result = [(i + 1) * base for i in range(parts - 1)]
        result.append(result[-1] + base + remainder if result else base + remainder)
        return result


    def makeEvalWorksheet(self):
        """Main function to create evaluation worksheet."""
        # Initialize headers and variables
        echoHeader = [
            'Harvest Time', 'Source Type', 'Source plate name', 'Source well', 'Transfer Volume',
            'Destination plate name', 'Targeting Well(forView)', 'Destination Well',
            'Destination Well X offset', 'Destination Well Y offset'
        ]
        shftHeader = [
            ';PlateType', 'PlateID', 'LocationShifter', 'PlateRow', 'PlateColumn',
            'PositionSubWell', 'Comment', 'CrystalID', 'TimeArrival', 'TimeDeparture',
            'PickDuration', 'DestinationName', 'DestinationLocation', 'Barcode', 'ExternalComment'
        ]

        plateType = self.PlateType.currentText()
        username = misc.getUsername()
        prjid = adpt.usernameToID(username)
        explist = getMxlive.expOnMxlive()[::-1]
        EvalueExpID = func.createExpID('pretest', 'new', self.crystname.text(), explist)
        pilot_list = misc.tableview_to_dict(self.pilot_tableview)
    
        # Classify entries by additive type
        classified_lists = self.classify_entries(pilot_list)
      
        # Initialize worklists
        shifter_worklist = [shftHeader]
        echo_worklist = {add_type: [] for add_type in classified_lists}
        echo_source_vol = {add_type:0 for add_type in classified_lists}
 
        # Populate Shifter and Echo worklists
        for add_type, entries in classified_lists.items():
            for entry in entries:
                harvest_time = entry['Time']
                transfer_vol = entry[add_type]
                target = entry['TargetWell']
                
                if target:
                    dest_plate = target.split('_')[0]
                    _dest_well =   target.split('_')[1]
                    dest_well = adpt.transPlate(plateType, _dest_well)
                    shft_row = self.generate_shifter_row(plateType, dest_plate, dest_well)
                    shifter_worklist.append(shft_row)
                    points = self.targetPoints.get(dest_plate, {}).get(_dest_well, None)
                    #point가 None인 경우 어떻게 해야 할지 모르겠음 하지만 그럴 리가 없긴 함
                    num_points = len(points)
                    #shots_per_point, shots_for_last_point = self.calculate_shots(transfer_vol, num_points)
                    shots_per_point = self.distribute_evenly( transfer_vol, num_points )                    
                    echo_rows = self.generate_echo_rows(harvest_time, add_type, shots_per_point, points, dest_well, dest_well[-1])
                    echo_worklist[add_type].extend(echo_rows)
                    echo_source_vol[add_type] += int(transfer_vol)
    
        dialog = MultiInputDialog(echo_source_vol)
        inputs = dialog.getInputs()
        if inputs:
            #for idx, input_text in enumerate(inputs, start=1):
            #    print(f"입력된 값 {idx}: {input_text}")
            for add_type, _wells in inputs.items():
                wells = _wells.replace(' ', '').split(',')
                wells = list(filter(bool, wells))
                print('!!', add_type, wells)
                echos = echo_worklist[add_type]
                reference_points = self.distribute_cumulative( echo_source_vol[add_type], len(wells) )                 
                used_vol = 0
                well_idx = 0
                for row in echos:
                    #add_type_fori_check = row[2]
                    vol = row[4]
                    used_vol += vol
                    #used_vol 값이 reference_points[i-1]보다 크고  reference_points[i]보다 작을 때, 
                    #source_well = wells[i]
                    if well_idx < len(reference_points) and used_vol > reference_points[well_idx]:
                        well_idx += 1  # Move to the next well when threshold is crossed 
                    source_well = wells[well_idx]
                    row[3] = source_well
                    print(row)
        else:
            print("취소됨")






        echo_worklist_full = []
        for add_type, worklist in echo_worklist.items():
                misc.listToCsv(PATHS['echo650'], f"{EvalueExpID}_{add_type}", worklist)
                echo_worklist_full.append(worklist)
        misc.listToCsv(PATHS['echo650'], f"{EvalueExpID}_full", echo_worklist_full)
        misc.listToCsv(PATHS['shifter1'], EvalueExpID, shifter_worklist)
        misc.listToCsv(PATHS['shifter2'], EvalueExpID, shifter_worklist)
        #return shifter_worklist, echo_worklist
        return 0










    def makeEvalWorksheet0(self):
        ### Final Result Form (Constant for this DeF)
        echoHeader = ['Harvest Time',\
                  'Source plate name', 'Source well', 'Transfer Volume', \
                  'Destination plate name', 'Targeting Well(forView)','Destination Well',\
                  'Destination Well X offset', 'Destination Well Y offset']

        shftHeader = [';PlateType', 'PlateID', 'LocationShifter', 'PlateRow', 'PlateColumn', 'PositionSubWell',\
                      'Comment', 'CrystalID', 'TimeArrival', 'TimeDeparture', 'PickDuration',\
                      'DestinationName', 'DestinationLocation', 'Barcode', 'ExternalComment']

        ### Variable Declaration
        plateType = self.PlateType.currentText()
        echoSolvList = [echoHeader]
        echoCryoList = [echoHeader]
        shifterList  = [shftHeader]
        username = misc.getUsername()
        prjid = adpt.usernameToID(username)
        DBrecord = []
        #Create Experiment ID
        explist = getMxlive.expOnMxlive()[::-1]
        EvalueExpID = func.createExpID('pretest', 'new',self.crystname.text(), explist)


        doSolvent, doCryopro, appendixes, durationVolume= self.scanEvalMethod()

        cnt = 0
        for key,val in self.targetPoints.items():
            try:
                #Assign A Chemical to key(imgKey)
                harvest = doSolvent[cnt][0]
                srcPlate = 'SourcePlate'
                srcType = doSolvent[cnt][1]
                srcWell = srcType
 
                destPlate = key.split('_')[0]
                destWell1 = key.split('_')[1]
                destWell2 = adpt.transPlate(plateType, destWell1)
                xtalRow = destWell1[0]
                xtalCol = str(int(destWell1[1:-1]))
                xtalSub = destWell1[-1]
                shftline = [plateType, destPlate, 'AM', xtalRow, xtalCol, xtalSub]
                shifterList.append(shftline)
                for i,item in enumerate(val):
                    #destPlate = key.split('_')[0]
                    #destWell1 = key.split('_')[1]
                    #destWell2 = adpt.transPlate(plateType, destWell1)
                    destYoffset = item[2]
                    if destWell1.endswith('d'): destXoffset = item[1] -700
                    else                      : destXoffset = item[1]
                    if doCryopro:
                        ctimes = float(doCryopro[cnt][2]) / 2.5 # 2.5ul : Minimum transfer value
                        cquota = ctimes // len(val)
                        cremain = ctimes % len(val)
                        if i+1 == len(val):
                            ctransVol = (cquota+cremain) * 2.5
                        else:
                            ctransVol = cquota * 2.5
                        echoCryoList.append( [doCryopro[cnt][0], srcPlate, doCryopro[cnt][1], ctransVol,\
                                          destPlate, destWell1, destWell2, destXoffset, destYoffset] )

                    if doSolvent:
                        #Assign Transfer volume to each value(points)
                        stimes = float(doSolvent[cnt][2]) / 2.5 # 2.5ul : Minimum transfer value
                        squota = stimes // len(val)
                        sremain = stimes % len(val)
                        if i+1 == len(val):
                            stransVol = (squota+sremain) * 2.5
                        else:
                            stransVol = squota * 2.5
                        echoSolvList.append( [harvest, srcPlate, srcWell, stransVol,\
                                             destPlate, destWell1, destWell2, destXoffset, destYoffset] )
                if doSolvent:
                    vol = doSolvent[cnt][2]
                    #if srcType.replace(' ' , '').lower() == 'dmso': smile = 'CS(=O)C'
                    #elif srcType.replace(' ' ,'').lower() == 'eg' or srcType == 'ethyleneglycol': smile = 'C(CO)O'
                    #else: smile = 'none'
                    if 'dmso' in srcType.replace(' ' , '').lower(): smile = 'CS(=O)C'
                    elif 'eg' in srcType.replace(' ' ,'').lower() or 'ethyleneglycol' in srcType.replace(' ' ,'').lower(): smile = 'C(CO)O'
                    else: smile = 'none' 
                elif doCryopro :
                    vol = doCryopro[cnt][2]
                    smile = 'none'
                else :
                    vol = 0
                    smile = 'none'
                record = { "name": username, \
                           "staff_comments": "upload by XtalViewer", "status": 5, "attachment": "null",\
                           "expri_id": EvalueExpID, "protein_name": self.crystname.text(), "plate_type": plateType,\
                           "plate_code": destPlate, "plate_imgpath": "none", "plate_well": destWell1,\
                           "plate_x": 0, "plate_y": 0, "crystal_no" : cnt+1,\
                           #Not matter in pretest
                           "soak_plate": 'pretest', "soak_well": 'Z00', "soak_vol": vol,\
                           "soak_id": appendixes[cnt], "soak_smile": smile, "project_id": prjid }
                DBrecord.append(record)
                cnt += 1
            except IndexError as e:
                pass
     
        echoPath = '{0}/{1}'.format(PATHS['echo650'], username)
        shft1Path = '{0}/{1}'.format(PATHS['shifter1'], username)
        shft2Path = '{0}/{1}'.format(PATHS['shifter2'], username)
        try:
            if len(echoSolvList) > 1:
                misc.listToCsv(echoPath, EvalueExpID, echoSolvList)
                print(doSolvent)
                print('[saveWorkSheet] ECHO worksheet for solvent(s): {0}'.format(echoPath))
            else: pass
            if len(echoCryoList) > 1:
                misc.listToCsv(echoPath, EvalueExpID+'_Cryo', echoCryoList)
                print('[saveWorkSheet] ECHO worksheet for cryoprotectant: {0}'.format(echoPath))
            else: pass
        except OSError:
            print('[saveWorkSheet] No such directory: {0}'.format(echoPath))
        except FileNotFoundError:
            print('[saveWorkSheet] Cannot write a file in {0}'.format(echoPath))

        try:
            if len(shifterList) > 1:
                misc.listToCsv(shft1Path, EvalueExpID, shifterList)
                print('[saveWorkSheet] SHIFTER worksheet: {0}'.format(shft1Path))
            else: pass
        except OSError:
            print('[saveWorkSheet] No such directory: {0}'.format(shft1Path))
        except FileNotFoundError:
            print('[saveWorkSheet] Cannot write a file in {0}'.format(shft1Path))


        try:
            if len(shifterList) > 1:
                misc.listToCsv(shft2Path, EvalueExpID, shifterList)
                print('[saveWorkSheet] SHIFTER worksheet: {0}'.format(shft2Path))
            else: pass
        except OSError:
            print('[saveWorkSheet] No such directory: {0}'.format(shft2Path))
        except FileNotFoundError:
            print('[saveWorkSheet] Cannot write a file in {0}'.format(shft2Path))

        for item in echoSolvList:
            print('[saveWorkSheet]',item)
        for item in echoCryoList:
            print('[saveCryoSheet]', item)
        for item in shifterList:
            print('[shifterList]', item)
        for item in DBrecord:
            print(item)
        return EvalueExpID, DBrecord





    def deliverToMxlive(self):
        sender = self.mainWidget.sender()
        if self.btnEvalueToMxlive == sender:
            expid, DBrecord = self.makeEvalWorksheet()
        elif self.btnScreenToMxlive == sender:
            expid, DBrecord = self.saveChemWorksheet()
        else: pass    

        jsonpath = func.deliverData(PATHS, expid, DBrecord)
        for item in sorted(os.listdir(jsonpath)):
            if item.endswith('json'):
                jsonfile = '{0}/{1}'.format(jsonpath, item)
                data = putMxlive.upload_labworks('BL-5C', jsonfile)
                print('[uploadtoMxlive]',data)
        return 0


    def saveChemWorksheet(self):
        username = misc.getUsername()
        prjid = adpt.usernameToID(username)
        echoPath = '{0}/{1}'.format(PATHS['echo650'], username)
        shft1Path = '{0}/{1}'.format(PATHS['shifter1'], username)
        shft2Path = '{0}/{1}'.format(PATHS['shifter2'], username)
        #if self.crystname.text().strip() == "":
        #    react = QMessageBox.warning(self,"Warning", "Give Protein name", QMessageBox.Yes)
        #else:
        #    defaultname = "target.csv"
        #    filesave = QFileDialog.getSaveFileName(self,"Save free target (csv)",\
        #                                           PATHS['userhome']+'/Documents/'+defaultname, "CSV Files (*.csv)")
        #    if filesave[0] != '':
        #        if filesave[0].endswith('.csv'):
        #            filename = filesave[0]
        #        else:
        #            filename = filesave[0] + '.csv'
        #        targetList = self.makeChemWorksheet()
        #        with open(filename, 'w') as f:
        #            csvWriter = csv.writer(f)
        #            for line in targetList:
        #                csvWriter.writerow(line)


        if self.crystname.text().strip() == "":
            react = QMessageBox.warning(self,"Warning", "Give Protein name", QMessageBox.Yes)
            screenExpID, echo650List, shifterList, DBrecord = '', [], [], []
        else:
            screenExpID, echo650List, shifterList, DBrecord = self.makeChemWorksheet()
            print(screenExpID, echo650List, shifterList, DBrecord)
            if len(echo650List) <= 1:
                print('[saveWorksheet] There is no target to send to Liquid Handler')
            elif len(shifterList) <= 1:
                print('[saveWorksheet] There is no target to send to SHIFTERs')
            else: 
                try:
                    misc.listToCsv(echoPath, screenExpID, echo650List)
                    print('[saveWorkSheet] Save ECHO worksheet: {0}'.format(echoPath))
                except OSError as e:
                    print('[saveWorksheet] {0}'.format(e))
                    print('[saveWorkSheet] No such directory: {0}'.format(echoPath))
                except FileNotFoundError as e:
                    print('[saveWorksheet] {0}'.format(e))
                    print('[saveWorkSheet] Cannot write a file in {0}'.format(echoPath))
                try:
                    misc.listToCsv(shft1Path, screenExpID, shifterList)
                    print('[saveWorkSheet] Save SHIFTER worksheet: {0}'.format(shft1Path))
                except OSError as e:
                    print('[saveWorksheet] {0}'.format(e))
                    print('[saveWorkSheet] No such directory: {0}'.format(shft1Path))
                except FileNotFoundError as e:
                    print('[saveWorksheet] {0}'.format(e))
                    print('[saveWorkSheet] Cannot write a file in {0}'.format(shft1Path))
                try:
                    misc.listToCsv(shft2Path, screenExpID, shifterList)
                    print('[saveWorkSheet] Save SHIFTER worksheet: {0}'.format(shft2Path))
                except OSError as e:
                    print('[saveWorksheet] {0}'.format(e))
                    print('[saveWorkSheet] No such directory: {0}'.format(shft2Path))
                except FileNotFoundError as e:
                    print('[saveWorksheet] {0}'.format(e))
                    print('[saveWorkSheet] Cannot write a file in {0}'.format(shft2Path))

        return screenExpID, DBrecord








    def makeChemWorksheet(self):
        ### Final Result Form (Constant for this DeF)
        echoHeader = ['Source plate name', 'Source well', 'Transfer Volume', \
                      'Destination plate name', 'Targeting Well(forView)','Destination Well',\
                      'Destination Well X offset', 'Destination Well Y offset']

        shftHeader = [';PlateType', 'PlateID', 'LocationShifter', 'PlateRow', 'PlateColumn', 'PositionSubWell',\
                      'Comment', 'CrystalID', 'TimeArrival', 'TimeDeparture', 'PickDuration',\
                      'DestinationName', 'DestinationLocation', 'Barcode', 'ExternalComment']

        ### Variabe Declaration
        username    = misc.getUsername()
        prjid       = adpt.usernameToID(username)
        explist     = getMxlive.expOnMxlive()[::-1]
        protein     = self.crystname.text().strip()
        screenExpID = func.createExpID('screen', 'new','noname', explist)
        plateType    = self.PlateType.currentText()
        echo650List  = [echoHeader]
        shifterList  = [shftHeader]
        DBrecord     = []
        if protein == "":
            react = QMessageBox.warning(self,"Warning", "Give Protein name", QMessageBox.Yes)
        
        else:
            screenExpID = func.createExpID('screen', 'new',protein, explist)
            ### Import User Input : from QLineEdit to List
            chemlist   = self.libraryInfo #It contains headerline
            chosenWell = self.getRegion()
            #Analyse header, ***make it case-insensitively!!
            #header = ['Vendor', 'Library', 'No', 'ID', 'Formula', 'MW', 'Smile', 'Conc_mM', 'Solvent', 'Plate_ID', 'Plate_well']
            idxPlateID = chemlist[0].index('Plate_ID')
            idxPlateWell = chemlist[0].index('Plate_well')
            idxChemID = chemlist[0].index('ID')
            idxSmile = chemlist[0].index('Smile')
            cnt = 0
            for key,val in self.targetPoints.items():
                try:
                    #Assign A Chemical to a key(imgKey)
                    idx = chosenWell[cnt]
                    srcPlate = chemlist[idx][idxPlateID]
                    srcWell  = chemlist[idx][idxPlateWell]
                    srcIDC   = chemlist[idx][idxChemID]
                    srcSmile = chemlist[idx][idxSmile]
                    totalVol = self.targetVol.value()
                    destPlate = key.split('_')[0]
                    destWell1 = key.split('_')[1]
                    destWell2 = adpt.transPlate(plateType, destWell1)
                    xtalRow = destWell1[0]
                    xtalCol = str(int(destWell1[1:-1]))
                    xtalSub = destWell1[-1]
                    shifterList.append( [plateType, destPlate, 'AM', xtalRow, xtalCol, xtalSub])
                    for i,item in enumerate(val):
                        destYoffset = item[2]
                        if destWell1.endswith('d'): destXoffset = item[1] -700
                        else                      : destXoffset = item[1]
                        #Assign Transfer volume to each value(points)
                        times = totalVol / 2.5 # 2.5ul : Minimum transfer value
                        quota = times // len(val)
                        remain = times % len(val)
                        if i+1 == len(val):
                            transVol = (quota+remain) * 2.5
                        else:
                            transVol = quota * 2.5
                        echo650List.append( [srcPlate, srcWell, transVol, destPlate, destWell1, destWell2, destXoffset, destYoffset] )

                    record = { "name": username, \
                               "staff_comments": "upload by XtalViewer", "status": 5, "attachment": "null",\
                               "expri_id": screenExpID, "protein_name": self.crystname.text(), "plate_type": plateType,\
                               "plate_code": destPlate, "plate_imgpath": "none", "plate_well": destWell1,\
                               "plate_x": 0, "plate_y": 0, "crystal_no" : cnt+1,\
                               "soak_plate": srcPlate, "soak_well": srcWell, "soak_vol": totalVol,\
                               "soak_id": srcIDC, "soak_smile": srcSmile, "project_id": prjid }
                    DBrecord.append(record)
                    cnt += 1
                except IndexError as e:
                    pass
            #remove it after stablized
            for item in echo650List:
                print('[makeChemWorksheet]',item)
            for item in shifterList:
                print('[makeChemWorksheet]',item)
            print('[makeChemWorksheet] {0} wells are Selected from the current library: {1}'.format(len(chosenWell), chosenWell))
        return screenExpID, echo650List, shifterList, DBrecord


    def makeCryoWorksheet(self):
        return 0
    def sendToInstrument(self):
        return 0
    def uploadToWebDB(self):
        return 0

    def clearChemPoints(self):
        return 0
    def clearCryoPoints(self):
        return 0



    def workSheets(self):
        #explist = [item for item in self.expOnWeb if item.startswith('Pre')]
        explist = getMxlive.expOnMxlive()[::-1]
        EvalueExpID = func.createExpID('pretest', 'new',self.crystname.text(), explist)
        print('[checkEXPID]', EvalueExpID)
    def shft1Finish(self):
        jsonpath = func.shft1Done(self.xtalsToScreen, self.ScreenExpID.currentText(), self.ScreenProtein.text(),PATHS)
        for item in sorted(os.listdir(jsonpath)):
            if item.startswith(self.ScreenExpID.currentText()) and item.endswith('json'):
                jsonfile = '{0}/{1}'.format(jsonpath, item)
                data = putMxlive.upload_labworks('BL-5C', jsonfile)
                print(data)
            else: pass

    def shft2Finish(self):
        jsonpath = func.shft2Done(self.xtalsToScreen, self.ScreenExpID.currentText(), self.ScreenProtein.text(),PATHS)
        for item in sorted(os.listdir(jsonpath)):
            if item.startswith(self.ScreenExpID.currentText()) and item.endswith('json'):
                jsonfile = '{0}/{1}'.format(jsonpath, item)
                data = putMxlive.upload_labworks('BL-5C', jsonfile)
                print(data)
            else: pass



    def readPlatesOnTable(self):
        # 0번 컬럼의 모든 plateID를 출력
        plate_ids = []
        for row in range(self.tableview_model.rowCount()):
            item = self.tableview_model.item(row, 0)  # 0번 컬럼의 아이템 가져오기
            if item:
                plate_ids.append(item.text())
    
        #print("Plate IDs:", plate_ids)  # 출력
        return plate_ids
 
    def importTargets(self):
        #이미지를 불러온 플레이트 한정으로 활용할 수 있어야 한다 / 수정이 필요함
        # 기본 경로 설정
        default_dir = PATHS['cellar']

        # 파일 다이얼로그 열기 (JSON 파일만 선택 가능)
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getOpenFileName(self, "Import Targets", default_dir, "JSON Files (*.json);;All Files (*)", options=options)

  
        if not file_path:
            print("No file Selected")
            return
        else:
            try:
                # JSON 파일 읽기
                with open(file_path, 'r', encoding='utf-8') as json_file:
                    imported_data = json.load(json_file)

                # 파일 이름을 키로 사용
                file_name = os.path.basename(file_path).replace('.json', '')

                #파일명이 platetable에 존재하는지 확인
                plates_on_table = self.readPlatesOnTable()
                if file_name in plates_on_table: pass
                else:

                    #존재 하지 않는 경우 추가
                    # Plate 정보를 로드하고 요약 저장
                    self.loadPlateInfo(rawList)

                    # 잘못된 plate 처리
                    self.handleInvalidPlates(wrongPlates)
                    self.refreshPlates() #self.targetPoints가 이 것 보다 먼저 업로드 되어야 한다


                if file_name in self.targetPoints:
                    # 키가 존재할 경우, Replace, Merge, Cancel 버튼을 제시
                    reply = QMessageBox.question(self, "Overwrite Existing Key", 
                                                 f"The key '{file_name}' already exists. What would you like to do?",
                                                 QMessageBox.Replace | QMessageBox.Merge | QMessageBox.Cancel)

                    if reply == QMessageBox.Replace:
                        # 덮어쓰기
                        self.targetPoints[file_name] = imported_data
                    elif reply == QMessageBox.Merge:
                        # 기존 값과 합치기
                        self.targetPoints[file_name].update(imported_data)  # 기존 딕셔너리와 합치기
                else:
                    # 새로운 키로 추가
                    self.targetPoints[file_name] = imported_data
                
                print(f'[import_targets] Imported: {file_name}')  # 가져온 파일 이름 출력

            except json.JSONDecodeError:
                QMessageBox.critical(self, "Error", "The selected file is not a valid JSON file.")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to import targets: {str(e)}")



    def exportTargets(self):
        # 현재 선택된 플레이트 번호를 가져옵니다
        current_row = self.pilot_tableview.currentIndex().row()
        plate_id = self.pilot_tableview_model.item(current_row, 0).text()
        save_time = datetime.now().strftime('%Y%m%d')
        # 현재 플레이트의 타겟을 저장합니다
        # 기본 저장 경로 및 파일명 설정
        default_dir = PATHS['cellar']
        #default_file_name = f"{default_dir}/{plate_id}.target_{num}"  # 기본 파일명 제시
        num = 1
        while True:
            file_name = f"{plate_id}.target_{num}"   
            file_path = os.path.join(default_dir, file_name)
            if not os.path.exists(file_path):
                break
            num += 1
        # 파일 다이얼로그에서 디렉토리와 파일명 선택
        options = QFileDialog.Options()
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Select File Name for Export",
            default_file_name,  # 기본 파일명
            "JSON Files (*.json)",
            options=options
        )
    
        if file_path:
            base_path = os.path.splitext(file_path)[0]  # 확장자를 제외한 기본 경로 추출
    
            for key, record in self.targetPoints.items():
                filename = f"{base_path}_{key}.json"  # 키를 파일명에 추가
    
                try:
                    with open(filename, 'w', encoding='utf-8') as json_file:
                        json.dump(record, json_file, ensure_ascii=False, indent="\t")
                    print(f'[export_target] Saved: {filename}')  # 저장된 파일 경로 출력
    
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Failed to export {key}: {str(e)}")
    


    def imageClickEvent(self, event):
        try:
            # Offset 계산을 함수로 분리
            Xoffset, Yoffset = self.calculateOffsets()
    
            zero = [(self.guideShift[0] + self.xCenter.value() + self.guideRadius + Xoffset) * self.imgScale.value() / 100,
                    (self.guideShift[1] + self.yCenter.value() + self.guideRadius + Yoffset) * self.imgScale.value() / 100]
  


            xPixcelPosition, yPixcelPosition = self.getPixelPosition(event, zero)
            xMeterPosition, yMeterPosition = self.getMeterPosition(event, zero)

    
            pID = self.currentPlate.text()
            well = self.currentWell.text()
            imgKey = f'{pID}_{well}'
            targetSites = self.divisions.value()
    
            if event.buttons() & Qt.LeftButton:
                if self.undoButton.isChecked():
                    self.handleEraserMode(xPixcelPosition, yPixcelPosition, pID, well)
                else:
                    #self.handleLeftClick(xMeterPosition, yMeterPosition, xPixcelPosition, yPixcelPosition, pID, well)
                    self.handleLeftClick(xMeterPosition, yMeterPosition)
            elif event.button() & Qt.RightButton:
                self.handleRightClick(xMeterPosition, yMeterPosition, xPixcelPosition, yPixcelPosition, imgKey, pID, well, targetSites, zero)
 
        except ValueError:
            pass
    
    # 값 변환하는 부분을 함수로 분리
    def calculateOffsets(self):
        row = int(self.currentWell.text()[1:3])
        col = ord(self.currentWell.text()[0]) - 65
        rowXoffset = row * self.colOffset[0] - self.colOffset[0]
        rowYoffset = row * self.colOffset[1] - self.colOffset[1]
        colXoffset = col * self.rowOffset[0]
        colYoffset = col * self.rowOffset[1]
        Xoffset = rowXoffset + colXoffset
        Yoffset = rowYoffset + colYoffset
        return Xoffset, Yoffset
    
    # 픽셀 좌표 계산 함수
    def getPixelPosition(self, event, zero):
        xPixcelPosition = (event.x() - zero[0]) / (self.imgScale.value() / 100)
        yPixcelPosition = (event.y() - zero[1]) / (self.imgScale.value() / 100)
        #return xPixcelPosition, yPixcelPosition
        #print('i need to see zero', zero)
        return event.x()/0.75, event.y()/0.75   
 
    # 미터 좌표 계산 함수
    def getMeterPosition(self, event, zero):
        xMeterPosition = (event.x() - zero[0]) * self.wellRadius / (self.guideRadius * self.imgScale.value() / 100)
        yMeterPosition = (event.y() - zero[1]) * self.wellRadius / (self.guideRadius * self.imgScale.value() / 100)
        return xMeterPosition, yMeterPosition

    def getDistance(self, x1, y1, x2, y2):
        ### Distance between two points
        
        try:
            # 좌표가 숫자인지 확인
            if not all(isinstance(coord, (int, float)) for coord in [x1, y1, x2, y2]):
                raise ValueError("[Error@getDistance] Coordinate Values are incorrect!")
            # 유클리드 거리 계산
            distance = math.sqrt((x1 - x2) ** 2 + (y1 - y2) ** 2)
            return distance
        except Exception as e:
            print(f"[Unknown@getDistance]: {e}")
            return None  # 오류가 발생하면 None을 반환


    
    # 좌클릭 핸들링 함수, 이용자에게 보여주기 위한 기능이므로 미터좌표만 사용.
    def handleLeftClick(self, new_x_um, new_y_um):
        #self.refPoints.append( [xMeterPosition, yMeterPositon] )
        text = f"LeftClick x:{round(new_x_um, 0)}um, y={round(new_y_um, 0)}um"
        self.window.statusbar.showMessage(text)

        if len(self.refPoints) == 0:
            self.refPoints.append( {'x_um':new_x_um, 'y_um':new_y_um} )
        else:
            old_x_um = self.refPoints[0]['x_um']
            old_y_um = self.refPoints[0]['y_um']
            distance = self.getDistance(old_x_um, old_y_um, new_x_um, new_y_um)
            self.window.distance.setText(str(round(distance)).rjust(4, ' ') + ' um')
            self.refPoints = []         

    
    # 지우개 모드 핸들링 함수
    def handleEraserMode(self, new_x_pix, new_y_pix, pID, well):
        radi = 30 *2 * (self.imgScale.value() / 100)
        to_remove = None
        try:
            for point in reversed(self.targetPoints[pID][well]):
                print('\nEraser', pID, well, point)
                selected_x = point['x_pixel']
                selected_y = point['y_pixel']
                distance = self.getDistance(selected_x, selected_y, new_x_pix, new_y_pix)
                print(selected_x, selected_y, new_x_pix, new_y_pix, distance)
                if distance <= radi:
                    to_remove = point
                    break
    
            if to_remove is not None:
                self.targetPoints[pID][well].remove(to_remove)
                text = f'Eraser: {len(self.targetPoints[pID][well])} point(s) remained in {well}@{pID}'
            else:
                text = 'No points found within range to erase.'
    
            # Check if the list is empty and update the button state
            if not self.targetPoints[pID][well]:
                self.undoButton.setChecked(False)
                self.targetCounts[pID] -= 1
                selected = sum( self.targetCounts.values() )
                self.window.nWells.setText( str(selected).rjust(4, ' ') )
            self.loadImage()
        except KeyError:
            text = 'No point(s) yet'
        except ValueError:
            text = 'Error: The point to remove was not found.'

        self.window.statusbar.showMessage(text)


    
    # 우클릭 핸들링 함수
    def handleRightClick(self, xMeterPosition, yMeterPosition, xPixcelPosition, yPixcelPosition, imgKey, pID, well, targetSites, zero):
        text = f"RightClick: x={round(xMeterPosition, 0)}um, y={round(yMeterPosition, 0)}um in {pID}_{well}"
        ### Make a Point
        po = { 'type':'', \
               'x_pixel': xPixcelPosition, \
               'y_pixel': yPixcelPosition, \
               'x_um':round(xMeterPosition, 0), \
               'y_um':round(yMeterPosition, 0)} 
        self.window.statusbar.showMessage(text)
       
        if pID not in self.targetPoints:
            self.targetPoints[pID] = {}
            self.targetCounts[pID] = 0
        if well in self.targetPoints[pID]:
            self.targetPoints[pID][well].append(po)
        else:
            self.targetPoints[pID][well] = [po]
            self.targetCounts[pID] += 1


        print('fin', self.targetPoints) 

        self.loadImage()


        #self.countSelected()
        #selected = sum( self.targetCounts.values() ) #only for self.targetPoints
        selected = sum( self.targetCounts.values() )
        self.window.nWells.setText( str(selected).rjust(4, ' ') )

        targetsInThisWell = len(self.targetPoints[pID][well])
        if targetsInThisWell >= targetSites:
            self.moveNextWell()
            
            if self.rightTab.currentIndex() == 0: #Evaluation Tab 
                #Pilot list에 plateid와 well을 표시한다
                self.add_well_toPilot(pID, well)
                pass
        


    def countSelected(self):
        for pID, wells in self.targetPoints.items():
            self.targetCounts[pID] = 0 
            for well, points in wells.items():
                if points: self.targetCounts[pID] += 1
        return 0



class MultiInputDialog(QDialog):
    def __init__(self, add_info, parent=None):
        super().__init__(parent)
        
        self.inputs = {}
        layout = QVBoxLayout()
        
        # label_texts 리스트를 순회하며 입력창과 레이블을 동적으로 생성
        for add_type, total_vol in add_info.items():
            h_layout = QHBoxLayout()
            label = QLabel(f"{add_type}의 총량은 {total_vol}nl입니다. SourceWell을 입력하세요")
            line_edit = QLineEdit()
            h_layout.addWidget(label)
            h_layout.addWidget(line_edit)
            layout.addLayout(h_layout)
            self.inputs[add_type] = line_edit

        # 확인 및 취소 버튼
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        
        layout.addWidget(button_box)
        self.setLayout(layout)


    def getInputs(self):
        # QDialog 실행 및 결과 반환
        if self.exec_() == QDialog.Accepted:
            #inputs = [line_edit.text() for line_edit in self.inputs]
            inputs = {add_type: line_edit.text() for add_type, line_edit in self.inputs.items()}
            print("입력된 값:", inputs)  # 디버깅을 위해 출력
            return inputs
        print("다이얼로그 취소됨")  # 취소 시 출력
        return None



class FloatLineEdit(QLineEdit):
    def __init__(self, initial_text=""):
        super().__init__()
        self.setText(initial_text)
        # textChanged 시그널을 set_text_color_based_on_value 함수에 연결
        self.textChanged.connect(self.set_text_color_based_on_value)

    def set_text_color_based_on_value(self):
        text = self.text().replace(' ', '').split(',')
        try:
            # 모든 값이 정수로 변환 가능한지 확인
            [int(value) for value in text]
            # 성공 시 글자색을 검은색으로 설정
            self.set_text_color("black")
        except ValueError:
            # 변환 불가 시 글자색을 빨간색으로 설정
            self.set_text_color("orange")

    def set_text_color(self, color):
        palette = self.palette()
        palette.setColor(QPalette.Text, QColor(color))
        self.setPalette(palette)



if __name__== '__main__':
    import sys
    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)
    #ex = MyApp()
    ex2 = index()
    sys.exit(app.exec_())



