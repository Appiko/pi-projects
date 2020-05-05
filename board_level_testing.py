#!/usr/bin/env python3


# Set the following environment variables
# - ERP_ENDPOINT - ERP endponint url (xyz.abc.com)
# - ERP_TOKEN - ERP token (token abz)
# - PROD_GQL_ENDPOINT - Prod graphQL endpoint
# - PROD_HASURA_KEY - Hasura key

import socket
import sys
import json
from gpiozero import Button, DigitalOutputDevice, LED
from signal import pause
from serial import Serial
from time import sleep, time
from datetime import date
import subprocess
from board import SCL, SDA
import requests
import busio
from PIL import Image, ImageDraw, ImageFont
import adafruit_ssd1306
import operator
from os import path, makedirs, environ


JIG_ID = 1
product_version = ""
manufacturer_id = ""
test_file_path = ""
prod_file_path = ""
current_board_id = ""

JLINK = "/home/pi/JLINK/JLinkExe -Speed 4000 -If SWD"
JLINK_ERASE_FILE = "/home/pi/erase.jlink"
JLINK_UPLOAD_TEST_FILE = "/home/pi/upload_test.jlink"
JLINK_UPLOAD_PROD_FILE = "/home/pi/upload_prod.jlink"

ERASEALL = f"{JLINK} {JLINK_ERASE_FILE}"
UPLOAD = f"{JLINK}"


APP = "sense_ele_BLT_s112"
BASE_URL = environ.get('ERP_ENDPOINT')


build_dir = ''

# UPLOAD_TESTING = f"{UPLOAD} {BUILD_DIR}/{APP}.hex;"
# UPLOAD_PROD = f"{UPLOAD} {OUTPUT_HEX}"


def intToHexStr(var):
    return format(var, 'x').zfill(2)


def new_board_id():
    global product_version
    id_date = date.today().strftime("%y%m%d")
    product = product_version.split('-')[0]
    version = product_version.split('-')[1].split('.')[0].zfill(2)
    return f"{product}{version}{manufacturer_id}{id_date}0000"


def get_board_id():
    global current_board_id
    url = f'{BASE_URL}/api/resource/Device?filters=[["product_version","=","{product_version}"],["manufacturer","=","{manufacturer_id}"], ["creation",">","{date.today().strftime("%Y-%m-%d")}"]]&order_by=creation desc&limit_page_length=1'
    r = make_req(url)
    try:
        if len(r['data'][0]['name']) > 0:
            s = r['data'][0]['name']
            current_board_id = s[:-4]+str(int(s[-4:]) + 1).zfill(4)
            return current_board_id
    except Exception:
        current_board_id = new_board_id()
        return current_board_id


def create_device():
    url = f"{BASE_URL}/api/resource/Device"
    payload = f'{{"device_id" : \"{get_board_id()}\","product_version": \"{product_version}\","manufacturer":\"{str(manufacturer_id)}\"}}'
    draw_text(current_board_id[-6:])
    make_post_req(url, payload)


def charToASCII(val):
    if isinstance(val, str):
        temp_lst = []
        lst = list(val)
        for i in range(len(lst)):
            temp_lst.append((ord(lst[i])))
            temp_lst[i] = intToHexStr(temp_lst[i])
        temp_lst = ''.join(temp_lst)
        return temp_lst


def split_len(seq, length):
    return [seq[i:i+length] for i in range(0, len(seq), length)]


def upload_testing():
    return analyze_output(subprocess.run(f'{UPLOAD} {JLINK_UPLOAD_TEST_FILE}', shell=True, check=True,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True).stdout)


def upload_prod():
    return analyze_output(subprocess.run(f'{UPLOAD} {JLINK_UPLOAD_PROD_FILE}', shell=True, check=True,
                                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True).stdout)


def erase_all():
    x = subprocess.run(ERASEALL, shell=True, check=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True).stdout
    return analyze_output(x)


def analyze_output(output_text):
    print(f'------------START\n{output_text}\n------------END')
    if "/dev/ttyBmpGdb: No such file or directory." in output_text:
        draw_two_lines("BMP", "loose connection")
        sys.exit()
    if "failed" in output_text:
        draw_two_lines("PCB not", "aligned")
        sys.exit()
    else:
        return True


def flash_testing_firmware():
    print("flashing testing firmware")
    if erase_all():
        return upload_testing()
    else:
        return False


def twos_complement(j):
    return abs(j-(1 << (j.bit_length())))


def get_hex_line(x):
    z = split_len(x, 2)
    val = 0
    for a in z:
        val = val+int(a, 16)
    chk_sum = format(twos_complement(val), 'x').zfill(2)[-2:]
    return ":"+x+chk_sum


def gen_product_hex():
    print("Product Id Generation : ")

    board_no = current_board_id
    product_id_reg = charToASCII(board_no)

    # Create and write to file
    product_hex_file = f'{build_dir}/product.hex'
    subprocess.run(f'touch {product_hex_file}', shell=True, check=True,
                   stdout=subprocess.PIPE, universal_newlines=True)
    product_hex = open(product_hex_file, "w")
    hex_file_contents = f''':020000041000EA
{get_hex_line("10108000"+product_id_reg)}
:00000001FF
'''
    product_hex.write(hex_file_contents)
    product_hex.close()


def merge_hex():
    merge_cmd = f"srec_cat {build_dir}/product.hex -Intel {prod_file_path} -Intel -O {build_dir}/output.hex -Intel --line-length=44"
    print(merge_cmd)
    analyze_output(subprocess.run(merge_cmd, shell=True, check=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True).stdout)


def flash_prod_firmware():
    print("Flashing production firmaware")
    gen_product_hex()
    merge_hex()
    pwr_reset()
    sleep(1)
    erase_all()
    upload_prod()
    pwr_reset()
    pwr_pin.off()
    print("DONE")


def get_test_dict(words):
    return {
        "system_component": words[0],
        "test_result": "Pass" if '1' in words[1] else "Fail",
        "test_type": "Board",
        "logs": "" if len(words) == 2 else ''.join(''.join(words[2:]).splitlines())
    }


def save_test_on_erp(tests, result):
    payload = {
        "is_board_passed": True if result == 1 else False,
        "device_id": current_board_id,
        "tests": tests
    }
    payload = json.dumps(payload)
    url = "https://appiko.iotready.co/api/resource/Device Test"
    make_post_req(url, payload)


def test():
    start_time = time()
    print("Looking UART values")
    last_line = ""
    if(ser.isOpen() == False):
        ser.open()
    tests = []
    while ((not "END" in last_line) and ((time() - start_time) < 30)):
        last_line = str(ser.readline(), 'ascii')
        print(last_line)
        if(len(last_line.split(','))) > 1:
            tests.append(get_test_dict(last_line.split(',')))

    ser.close()
    pwr_pin.off()

    if "1" in last_line:
        print("PASS")
        save_test_on_erp(tests, 1)
        return True
    else:
        print("FAIL")
        save_test_on_erp(tests, 0)
        return False


def start_testing():
    if flash_testing_firmware():
        pwr_reset()
    else:
        return False


def turn_on(val):
    led_red.off()
    led_green.off()
    if(val == 0):
        led_red.on()
    else:
        led_green.on()


def update_hasura():
    mutation = f"mutation MyMutation {{insert_board(objects: {{id: \"{current_board_id}\"}}) {{affected_rows}}}}"
    payload = json.dumps({'query': mutation})
    print(payload)
    try:
        r = requests.post(
            environ.get('PROD_GQL_ENDPOINT'), headers={'accept': 'application/json', 'x-hasura-admin-secret': environ.get('PROD_HASURA_KEY'), 'Content-Type': 'application/json'}, data=payload)
        if r.status_code == 200:
            print(r.json())
        else:
            print(f"Err, {r.status_code}")
            print(r.content)
            raise Exception()
    except Exception as e:
        print(e)
        draw_two_lines("Server error", "retry later?")
        sys.exit()


def button_pressed():
    led_green.blink(0.5, 0.5)
    sleep(0.5)
    led_red.blink(0.5, 0.5)
    pwr_pin.on()
    create_device()
    start_testing()
    test_pass = test()
    if(test_pass):
        flash_prod_firmware()
        update_hasura()
        turn_on(1)
    else:
        print("FAIL")
        turn_on(0)


def pwr_reset():
    print("resetting power")
    pwr_pin.off()
    sleep(0.5)
    pwr_pin.on()


def draw_text(text):
    font = ImageFont.truetype('/home/pi/.fonts/VCR_OSD_MONO_1.001.ttf', 24)
    (font_w, font_h) = font.getsize(text)
    draw.rectangle((0, 0, width, height), outline=0, fill=0)
    draw.text((((width/2) - (font_w/2)), ((height/2)-(font_h/2))),
              text, font=font, fill=255)
    disp.image(image)
    disp.show()


def draw_two_lines(line_one, line_two):
    font = ImageFont.truetype('/home/pi/.fonts/VCR_OSD_MONO_1.001.ttf', 16)
    (one_w, one_h) = font.getsize(line_one)
    (two_w, two_h) = font.getsize(line_two)

    (font_w, font_h) = (max(one_w, two_w), (one_h+two_h))

    draw.rectangle((0, 0, width, height), outline=0, fill=0)
    draw.text((((width/2) - (font_w/2)), ((height/2)-(font_h/2)) - 8),
              line_one, font=font, fill=255)
    draw.text((((width/2) - (font_w/2)), ((height/2)-(font_h/2)) + 8),
              line_two, font=font, fill=255)
    disp.image(image)
    disp.show()


def is_connected():
    try:
        socket.create_connection(("www.google.com", 80))
        return True
    except OSError:
        draw_two_lines("No Internet?", "AP/12345678")
        sys.exit()
    return False


def make_post_req(url, payload):
    try:
        r = requests.post(
            url, headers={'accept': 'application/json', 'Authorization': environ.get('ERP_TOKEN'), 'Content-Type': 'application/json'}, data=payload)
        if r.status_code == 200:
            return r.json()
        else:
            print(f"Err, {r.status_code}")
            print(r.content)
            raise Exception()
    except Exception as e:
        print(e)
        draw_two_lines("Server error", "retry later?")
        sys.exit()


def make_req(url):
    try:
        r = requests.get(
            url, headers={'Authorization': environ.get('ERP_TOKEN')})
        if r.status_code == 200:
            return r.json()
        else:
            print(f"Err, {r.status_code}")
            raise Exception()
    except Exception as e:
        print(e)
        draw_two_lines("Server error", "retry later?")
        sys.exit()


def req_download(file_url):
    try:
        data = f'{{"file_url":"{file_url}"}}'
        r = requests.get(
            f'{BASE_URL}/api/method/frappe.core.doctype.file.file.download_file', headers={'Authorization': environ.get('ERP_TOKEN'), 'Content-Type': 'application/json'}, data=data)
        if r.status_code == 200:
            return r.content
        else:
            print(f"Err, {r.status_code}")
            raise Exception()
    except Exception as e:
        print(e)
        draw_two_lines("Server error", "retry later?")
        sys.exit()


def get_info_for_jig():
    global manufacturer_id
    global product_version
    r = make_req(f'{BASE_URL}/api/resource/Jig/{JIG_ID}')
    manufacturer_id = r['data']['manufacturer']
    product_version = r['data']['product_version']


def download_hex_files():
    global test_firmware_url
    global prod_firmware_url
    global test_file_path
    global prod_file_path
    global build_dir

    test_file_path = f"/home/pi/board_level_testing/{product_version}/test.hex"
    prod_file_path = f"/home/pi/board_level_testing/{product_version}/prod.hex"

    build_dir = path.dirname(prod_file_path)

    f = open(JLINK_UPLOAD_TEST_FILE, 'w')
    test_file_contents = f'''device NRF52810_XXAA
w4 4001E504 1
loadfile {test_file_path}
r
g
qc'''
    f.write(test_file_contents)
    f.close()

    f = open(JLINK_UPLOAD_PROD_FILE, 'w')
    file_contents = f'''device NRF52810_XXAA
w4 4001E504 1
loadfile {build_dir}/output.hex
r
g
qc'''
    f.write(file_contents)
    f.close()
    if not path.exists(prod_file_path):
        try:
            makedirs(path.dirname(test_file_path))
            r = make_req(
                f'{BASE_URL}/api/resource/Product Version/{product_version}')
            test_firmware_url = r["data"]["test_firmware"]
            prod_firmware_url = r["data"]["production_firmware"]
            f = open(test_file_path, "wb")
            f.write(req_download(test_firmware_url))
            f.close()
            fx = open(prod_file_path, "wb")
            fx.write(req_download(prod_firmware_url))
            fx.close()

        except Exception as e:
            print(e)
            sys.exit()
    else:
        return


ser = Serial()

ser.baudrate = 1000000
ser.port = '/dev/ttyACM0'
ser.timeout = 3

i2c = busio.I2C(SCL, SDA)
disp = adafruit_ssd1306.SSD1306_I2C(128, 64, i2c, addr=0x3c)


# Clear display.
disp.fill(0)
disp.show()

width = disp.width
height = disp.height
image = Image.new('1', (width, height))
draw = ImageDraw.Draw(image)
draw.rectangle((0, 0, width, height), outline=1, fill=0)
draw_two_lines("Hello,", "World")

if(is_connected):
    get_info_for_jig()
    download_hex_files()

    button = Button(17)

    led_green = LED(22, False)
    led_red = LED(27, False)

    led_green.on()

    pwr_pin = DigitalOutputDevice(4, False)
    button.when_activated = button_pressed

    pause()
