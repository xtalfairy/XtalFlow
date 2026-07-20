import os
import sys
import csv


def transPlate(plate, well):
    #plateGrid = {"row" : ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H'], \
    #             "col" : [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12], \
    #             "baby": ['a','x', 'c','d']}
    #GridSwiss3 = []
    #for i in plateGrid["row"]:
    #    for j in plateGrid["col"]:
    #        for k in ['a', 'x']:
    #            w = '{0}{1}{2}'.format(i,str(j).zfill(2), k)
    #            GridSwiss3.append(w)
    #    for j in plateGrid["col"]:
    #        for k in ['c', 'd']:
    #            w = '{0}{1}{2}'.format(i,str(j).zfill(2), k)
    #            GridSwiss3.append(w)
    #Grid384 = []
    #for i in range(1,25):
    #    for j in range(65, 81):
    #        w384 = '{0}{1}'.format(chr(j),str(i).zfill(2))
    #        Grid384.append(w384)

    GridSwiss3 = ['A01a', 'A01x', 'A02a', 'A02x', 'A03a', 'A03x', 'A04a', 'A04x', 'A05a', 'A05x', 'A06a', 'A06x', 'A07a', 'A07x', 'A08a', 'A08x', 'A09a', 'A09x', 'A10a', 'A10x', 'A11a', 'A11x', 'A12a', 'A12x',\
                  'A01c', 'A01d', 'A02c', 'A02d', 'A03c', 'A03d', 'A04c', 'A04d', 'A05c', 'A05d', 'A06c', 'A06d', 'A07c', 'A07d', 'A08c', 'A08d', 'A09c', 'A09d', 'A10c', 'A10d', 'A11c', 'A11d', 'A12c', 'A12d',\
                  'B01a', 'B01x', 'B02a', 'B02x', 'B03a', 'B03x', 'B04a', 'B04x', 'B05a', 'B05x', 'B06a', 'B06x', 'B07a', 'B07x', 'B08a', 'B08x', 'B09a', 'B09x', 'B10a', 'B10x', 'B11a', 'B11x', 'B12a', 'B12x',\
                  'B01c', 'B01d', 'B02c', 'B02d', 'B03c', 'B03d', 'B04c', 'B04d', 'B05c', 'B05d', 'B06c', 'B06d', 'B07c', 'B07d', 'B08c', 'B08d', 'B09c', 'B09d', 'B10c', 'B10d', 'B11c', 'B11d', 'B12c', 'B12d',\
                  'C01a', 'C01x', 'C02a', 'C02x', 'C03a', 'C03x', 'C04a', 'C04x', 'C05a', 'C05x', 'C06a', 'C06x', 'C07a', 'C07x', 'C08a', 'C08x', 'C09a', 'C09x', 'C10a', 'C10x', 'C11a', 'C11x', 'C12a', 'C12x',\
                  'C01c', 'C01d', 'C02c', 'C02d', 'C03c', 'C03d', 'C04c', 'C04d', 'C05c', 'C05d', 'C06c', 'C06d', 'C07c', 'C07d', 'C08c', 'C08d', 'C09c', 'C09d', 'C10c', 'C10d', 'C11c', 'C11d', 'C12c', 'C12d',\
                  'D01a', 'D01x', 'D02a', 'D02x', 'D03a', 'D03x', 'D04a', 'D04x', 'D05a', 'D05x', 'D06a', 'D06x', 'D07a', 'D07x', 'D08a', 'D08x', 'D09a', 'D09x', 'D10a', 'D10x', 'D11a', 'D11x', 'D12a', 'D12x',\
                  'D01c', 'D01d', 'D02c', 'D02d', 'D03c', 'D03d', 'D04c', 'D04d', 'D05c', 'D05d', 'D06c', 'D06d', 'D07c', 'D07d', 'D08c', 'D08d', 'D09c', 'D09d', 'D10c', 'D10d', 'D11c', 'D11d', 'D12c', 'D12d',\
                  'E01a', 'E01x', 'E02a', 'E02x', 'E03a', 'E03x', 'E04a', 'E04x', 'E05a', 'E05x', 'E06a', 'E06x', 'E07a', 'E07x', 'E08a', 'E08x', 'E09a', 'E09x', 'E10a', 'E10x', 'E11a', 'E11x', 'E12a', 'E12x',\
                  'E01c', 'E01d', 'E02c', 'E02d', 'E03c', 'E03d', 'E04c', 'E04d', 'E05c', 'E05d', 'E06c', 'E06d', 'E07c', 'E07d', 'E08c', 'E08d', 'E09c', 'E09d', 'E10c', 'E10d', 'E11c', 'E11d', 'E12c', 'E12d',\
                  'F01a', 'F01x', 'F02a', 'F02x', 'F03a', 'F03x', 'F04a', 'F04x', 'F05a', 'F05x', 'F06a', 'F06x', 'F07a', 'F07x', 'F08a', 'F08x', 'F09a', 'F09x', 'F10a', 'F10x', 'F11a', 'F11x', 'F12a', 'F12x',\
                  'F01c', 'F01d', 'F02c', 'F02d', 'F03c', 'F03d', 'F04c', 'F04d', 'F05c', 'F05d', 'F06c', 'F06d', 'F07c', 'F07d', 'F08c', 'F08d', 'F09c', 'F09d', 'F10c', 'F10d', 'F11c', 'F11d', 'F12c', 'F12d',\
                  'G01a', 'G01x', 'G02a', 'G02x', 'G03a', 'G03x', 'G04a', 'G04x', 'G05a', 'G05x', 'G06a', 'G06x', 'G07a', 'G07x', 'G08a', 'G08x', 'G09a', 'G09x', 'G10a', 'G10x', 'G11a', 'G11x', 'G12a', 'G12x',\
                  'G01c', 'G01d', 'G02c', 'G02d', 'G03c', 'G03d', 'G04c', 'G04d', 'G05c', 'G05d', 'G06c', 'G06d', 'G07c', 'G07d', 'G08c', 'G08d', 'G09c', 'G09d', 'G10c', 'G10d', 'G11c', 'G11d', 'G12c', 'G12d',\
                  'H01a', 'H01x', 'H02a', 'H02x', 'H03a', 'H03x', 'H04a', 'H04x', 'H05a', 'H05x', 'H06a', 'H06x', 'H07a', 'H07x', 'H08a', 'H08x', 'H09a', 'H09x', 'H10a', 'H10x', 'H11a', 'H11x', 'H12a', 'H12x',\
                  'H01c', 'H01d', 'H02c', 'H02d', 'H03c', 'H03d', 'H04c', 'H04d', 'H05c', 'H05d', 'H06c', 'H06d', 'H07c', 'H07d', 'H08c', 'H08d', 'H09c', 'H09d', 'H10c', 'H10d', 'H11c', 'H11d', 'H12c', 'H12d']

    GridSwiss2 = ['A01x', 'A01a', 'A02x', 'A02a', 'A03x', 'A03a', 'A04x', 'A04a', 'A05x', 'A05a', 'A06x', 'A06a', 'A07x', 'A07a', 'A08x', 'A08a', 'A09x', 'A09a', 'A10x', 'A10a', 'A11x', 'A11a', 'A12x', 'A12a',\
                  'A01y', 'A01b', 'A02y', 'A02b', 'A03y', 'A03b', 'A04y', 'A04b', 'A05y', 'A05b', 'A06y', 'A06b', 'A07y', 'A07b', 'A08y', 'A08b', 'A09y', 'A09b', 'A10y', 'A10b', 'A11y', 'A11b', 'A12y', 'A12b',\
                  'B01x', 'B01a', 'B02x', 'B02a', 'B03x', 'B03a', 'B04x', 'B04a', 'B05x', 'B05a', 'B06x', 'B06a', 'B07x', 'B07a', 'B08x', 'B08a', 'B09x', 'B09a', 'B10x', 'B10a', 'B11x', 'B11a', 'B12x', 'B12a',\
                  'B01y', 'B01b', 'B02y', 'B02b', 'B03y', 'B03b', 'B04y', 'B04b', 'B05y', 'B05b', 'B06y', 'B06b', 'B07y', 'B07b', 'B08y', 'B08b', 'B09y', 'B09b', 'B10y', 'B10b', 'B11y', 'B11b', 'B12y', 'B12b',\
                  'C01x', 'C01a', 'C02x', 'C02a', 'C03x', 'C03a', 'C04x', 'C04a', 'C05x', 'C05a', 'C06x', 'C06a', 'C07x', 'C07a', 'C08x', 'C08a', 'C09x', 'C09a', 'C10x', 'C10a', 'C11x', 'C11a', 'C12x', 'C12a',\
                  'C01y', 'C01b', 'C02y', 'C02b', 'C03y', 'C03b', 'C04y', 'C04b', 'C05y', 'C05b', 'C06y', 'C06b', 'C07y', 'C07b', 'C08y', 'C08b', 'C09y', 'C09b', 'C10y', 'C10b', 'C11y', 'C11b', 'C12y', 'C12b',\
                  'D01x', 'D01a', 'D02x', 'D02a', 'D03x', 'D03a', 'D04x', 'D04a', 'D05x', 'D05a', 'D06x', 'D06a', 'D07x', 'D07a', 'D08x', 'D08a', 'D09x', 'D09a', 'D10x', 'D10a', 'D11x', 'D11a', 'D12x', 'D12a',\
                  'D01y', 'D01b', 'D02y', 'D02b', 'D03y', 'D03b', 'D04y', 'D04b', 'D05y', 'D05b', 'D06y', 'D06b', 'D07y', 'D07b', 'D08y', 'D08b', 'D09y', 'D09b', 'D10y', 'D10b', 'D11y', 'D11b', 'D12y', 'D12b',\
                  'E01x', 'E01a', 'E02x', 'E02a', 'E03x', 'E03a', 'E04x', 'E04a', 'E05x', 'E05a', 'E06x', 'E06a', 'E07x', 'E07a', 'E08x', 'E08a', 'E09x', 'E09a', 'E10x', 'E10a', 'E11x', 'E11a', 'E12x', 'E12a',\
                  'E01y', 'E01b', 'E02y', 'E02b', 'E03y', 'E03b', 'E04y', 'E04b', 'E05y', 'E05b', 'E06y', 'E06b', 'E07y', 'E07b', 'E08y', 'E08b', 'E09y', 'E09b', 'E10y', 'E10b', 'E11y', 'E11b', 'E12y', 'E12b',\
                  'F01x', 'F01a', 'F02x', 'F02a', 'F03x', 'F03a', 'F04x', 'F04a', 'F05x', 'F05a', 'F06x', 'F06a', 'F07x', 'F07a', 'F08x', 'F08a', 'F09x', 'F09a', 'F10x', 'F10a', 'F11x', 'F11a', 'F12x', 'F12a',\
                  'F01y', 'F01b', 'F02y', 'F02b', 'F03y', 'F03b', 'F04y', 'F04b', 'F05y', 'F05b', 'F06y', 'F06b', 'F07y', 'F07b', 'F08y', 'F08b', 'F09y', 'F09b', 'F10y', 'F10b', 'F11y', 'F11b', 'F12y', 'F12b',\
                  'G01x', 'G01a', 'G02x', 'G02a', 'G03x', 'G03a', 'G04x', 'G04a', 'G05x', 'G05a', 'G06x', 'G06a', 'G07x', 'G07a', 'G08x', 'G08a', 'G09x', 'G09a', 'G10x', 'G10a', 'G11x', 'G11a', 'G12x', 'G12a',\
                  'G01y', 'G01b', 'G02y', 'G02b', 'G03y', 'G03b', 'G04y', 'G04b', 'G05y', 'G05b', 'G06y', 'G06b', 'G07y', 'G07b', 'G08y', 'G08b', 'G09y', 'G09b', 'G10y', 'G10b', 'G11y', 'G11b', 'G12y', 'G12b',\
                  'H01x', 'H01a', 'H02x', 'H02a', 'H03x', 'H03a', 'H04x', 'H04a', 'H05x', 'H05a', 'H06x', 'H06a', 'H07x', 'H07a', 'H08x', 'H08a', 'H09x', 'H09a', 'H10x', 'H10a', 'H11x', 'H11a', 'H12x', 'H12a',\
                  'H01y', 'H01b', 'H02y', 'H02b', 'H03y', 'H03b', 'H04y', 'H04b', 'H05y', 'H05b', 'H06y', 'H06b', 'H07y', 'H07b', 'H08y', 'H08b', 'H09y', 'H09b', 'H10y', 'H10b', 'H11y', 'H11b', 'H12y', 'H12d']


    Grid384 = ['A01', 'A02', 'A03', 'A04', 'A05', 'A06', 'A07', 'A08', 'A09', 'A10', 'A11', 'A12', 'A13', 'A14', 'A15', 'A16', 'A17', 'A18', 'A19', 'A20', 'A21', 'A22', 'A23', 'A24', \
               'B01', 'B02', 'B03', 'B04', 'B05', 'B06', 'B07', 'B08', 'B09', 'B10', 'B11', 'B12', 'B13', 'B14', 'B15', 'B16', 'B17', 'B18', 'B19', 'B20', 'B21', 'B22', 'B23', 'B24', \
               'C01', 'C02', 'C03', 'C04', 'C05', 'C06', 'C07', 'C08', 'C09', 'C10', 'C11', 'C12', 'C13', 'C14', 'C15', 'C16', 'C17', 'C18', 'C19', 'C20', 'C21', 'C22', 'C23', 'C24', \
               'D01', 'D02', 'D03', 'D04', 'D05', 'D06', 'D07', 'D08', 'D09', 'D10', 'D11', 'D12', 'D13', 'D14', 'D15', 'D16', 'D17', 'D18', 'D19', 'D20', 'D21', 'D22', 'D23', 'D24', \
               'E01', 'E02', 'E03', 'E04', 'E05', 'E06', 'E07', 'E08', 'E09', 'E10', 'E11', 'E12', 'E13', 'E14', 'E15', 'E16', 'E17', 'E18', 'E19', 'E20', 'E21', 'E22', 'E23', 'E24', \
               'F01', 'F02', 'F03', 'F04', 'F05', 'F06', 'F07', 'F08', 'F09', 'F10', 'F11', 'F12', 'F13', 'F14', 'F15', 'F16', 'F17', 'F18', 'F19', 'F20', 'F21', 'F22', 'F23', 'F24', \
               'G01', 'G02', 'G03', 'G04', 'G05', 'G06', 'G07', 'G08', 'G09', 'G10', 'G11', 'G12', 'G13', 'G14', 'G15', 'G16', 'G17', 'G18', 'G19', 'G20', 'G21', 'G22', 'G23', 'G24', \
               'H01', 'H02', 'H03', 'H04', 'H05', 'H06', 'H07', 'H08', 'H09', 'H10', 'H11', 'H12', 'H13', 'H14', 'H15', 'H16', 'H17', 'H18', 'H19', 'H20', 'H21', 'H22', 'H23', 'H24', \
               'I01', 'I02', 'I03', 'I04', 'I05', 'I06', 'I07', 'I08', 'I09', 'I10', 'I11', 'I12', 'I13', 'I14', 'I15', 'I16', 'I17', 'I18', 'I19', 'I20', 'I21', 'I22', 'I23', 'I24', \
               'J01', 'J02', 'J03', 'J04', 'J05', 'J06', 'J07', 'J08', 'J09', 'J10', 'J11', 'J12', 'J13', 'J14', 'J15', 'J16', 'J17', 'J18', 'J19', 'J20', 'J21', 'J22', 'J23', 'J24', \
               'K01', 'K02', 'K03', 'K04', 'K05', 'K06', 'K07', 'K08', 'K09', 'K10', 'K11', 'K12', 'K13', 'K14', 'K15', 'K16', 'K17', 'K18', 'K19', 'K20', 'K21', 'K22', 'K23', 'K24', \
               'L01', 'L02', 'L03', 'L04', 'L05', 'L06', 'L07', 'L08', 'L09', 'L10', 'L11', 'L12', 'L13', 'L14', 'L15', 'L16', 'L17', 'L18', 'L19', 'L20', 'L21', 'L22', 'L23', 'L24', \
               'M01', 'M02', 'M03', 'M04', 'M05', 'M06', 'M07', 'M08', 'M09', 'M10', 'M11', 'M12', 'M13', 'M14', 'M15', 'M16', 'M17', 'M18', 'M19', 'M20', 'M21', 'M22', 'M23', 'M24', \
               'N01', 'N02', 'N03', 'N04', 'N05', 'N06', 'N07', 'N08', 'N09', 'N10', 'N11', 'N12', 'N13', 'N14', 'N15', 'N16', 'N17', 'N18', 'N19', 'N20', 'N21', 'N22', 'N23', 'N24', \
               'O01', 'O02', 'O03', 'O04', 'O05', 'O06', 'O07', 'O08', 'O09', 'O10', 'O11', 'O12', 'O13', 'O14', 'O15', 'O16', 'O17', 'O18', 'O19', 'O20', 'O21', 'O22', 'O23', 'O24', \
               'P01', 'P02', 'P03', 'P04', 'P05', 'P06', 'P07', 'P08', 'P09', 'P10', 'P11', 'P12', 'P13', 'P14', 'P15', 'P16', 'P17', 'P18', 'P19', 'P20', 'P21', 'P22', 'P23', 'P24']

    if plate == 'SwissCI-MRC-3d':
        no = GridSwiss3.index(well)
    elif plate == 'SwissCI-MRC-2d':
        no = GridSwiss2.index(well)
    else: pass
    trans = Grid384[no]
    return trans



def usernameToID(name):
    #user = { 1:'admin',\
    #        37:'chj',\
    #        38:'jjh',\
    #        39:'ecs',\
    #        40:'cauphh',\
    #        41:'snuchj',\
    #        42:'kaerikmk',\
    #        43:'kribbbk',\
    #        44:'dgmif',\
    #        45:'ckd',\
    #        46:'kbrihhlim',\
    #        47:'linwoo',\
    #        49:'kuhks',\
    #        51:'nccbilee',\
    #        53:'unistkcu',\
    #        54:'gisteom',\
    #        55:'ewhacha',\
    #        56:'poscho',\
    #        57:'unilcw',\
    #        58:'knujhc',\
    #        59:'yuwtl',\
    #        60:'dongaysm',\
    #        61:'kaistkhs',\
    #        62:'snulbj',\
    #        63:'knukbs',\
    #        64:'csushlee',\
    #        65:'kuhwang',\
    #        66:'skkukyh',\
    #        67:'kopjhlee',\
    #        68:'cgijwc',\
    #        69:'dgujylee',\
    #        70:'kujeon',\
    #        71:'dmuybx',\
    #        72:'kistchs',\
    #        73:'kistkek',\
    #        74:'snuhnc',\
    #        75:'11c',\
    #        76:'kuhys',\
    #        77:'posmsk',\
    #        78:'gistmsj',\
    #        79:'gnulkh',\
    #        80:'kaistbhoh',\
    #        81:'lgysj',\
    #        82:'snuhbw',\
    #        83:'b2shlk',\
    #        85:'lsjlsjj',\
    #        87:'palnam',\
    #        89:'snulhh',\
    #        90:'bl5c',\
    #        91:'gistjwk',\
    #        92:'ncchsk',\
    #        93:'snujsk',\
    #        94:'kribb_shin',\
    #        95:'yucho',\
    #        97:'jlee',\
    #        98:'dyokim',\
    #        99:'inseong89',\
    #        100:'hyunkbri',\
    #        101:'snush',\
    #        102:'kribb_hwang',\
    #        103:'postechljo',\
    #        104:'mdseo',\
    #        105:'ssjinn',\
    #        106:'snujy',\
    #        107:'uosjc',\
    #        108:'shg',\
    #        109:'kbiolee',\
    #        110:'mogam',\
    #        111:'niccolo',\
    #        112:'kyg',\
    #        113:'snuswj',\
    #        114:'promedigen'}
    #user_rev = {v:k for k,v in user.items()}
    user = {'admin': 1,\
            'chj': 37, 'jjh': 38, 'ecs': 39,\
            'cauphh': 40, 'snuchj': 41, 'kaerikmk': 42, 'kribbbk': 43, 'dgmif': 44,\
            'ckd': 45, 'kbrihhlim': 46, 'linwoo': 47, 'kuhks': 49, 'nccbilee': 51,\
            'unistkcu': 53, 'gisteom': 54, 'ewhacha': 55, 'poscho': 56, 'unilcw': 57,\
            'knujhc': 58, 'yuwtl': 59, 'dongaysm': 60, 'kaistkhs': 61, 'snulbj': 62,\
            'knukbs': 63, 'csushlee': 64, 'kuhwang': 65, 'skkukyh': 66, 'kopjhlee': 67,\
            'cgijwc': 68, 'dgujylee': 69, 'kujeon': 70, 'dmuybx': 71, 'kistchs': 72,\
            'kistkek': 73, 'snuhnc': 74, '11c': 75, 'kuhys': 76, 'posmsk': 77,\
            'gistmsj': 78, 'gnulkh': 79, 'kaistbhoh': 80, 'lgysj': 81, 'snuhbw': 82,\
            'b2shlk': 83, 'lsjlsjj': 85, 'palnam': 87, 'snulhh': 89, 'bl5c': 90,\
            'gistjwk': 91, 'ncchsk': 92, 'snujsk': 93, 'kribb_shin': 94, 'yucho': 95,\
            'jlee': 97, 'dyokim': 98, 'inseong89': 99, 'hyunkbri': 100, 'snush': 101,\
            'kribb_hwang': 102, 'postechljo': 103, 'mdseo': 104, 'ssjinn': 105, 'snujy': 106,\
            'uosjc': 107, 'shg': 108, 'kbiolee': 109, 'mogam': 110, 'niccolo': 111,\
            'kyg': 112, 'snuswj': 113, 'promedigen': 114, 'kistlik':120, 'ybioldw':121,\
            'fbdd': 122, 'kbsihykim':123, 'jbnuslee026':125, 'palsj':126, 'oscotec':127,\
            'ysu_dhshin':128, 'CBNUCHJ':129, 'azcuris':132, 'curogen':133, 'novorex':142}
    userid = user[name]
    return userid
