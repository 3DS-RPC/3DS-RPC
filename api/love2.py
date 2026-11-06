# Created by Deltaion Lee (MCMi460) on Github
# Convert QR Code (mii_data) format to CFSD
# And convert CFSD to Mii Studio

# This time, it only took me like one week to figure out
# Thank you, https://www.3dbrew.org/wiki/Mii and https://www.3dbrew.org/wiki/Mii_Maker
# for helping me figure out that mii_data is literally just a QR code
# I dunno why
# More importantly, thank you to jaames (https://gist.github.com/jaames)
# for https://gist.github.com/jaames/96ce8daa11b61b758b6b0227b55f9f78

# And thank you to HEYimHeroic
# for https://github.com/HEYimHeroic/mii2studio/blob/master/mii2studio.py

import io
from binascii import hexlify
from struct import pack

# Decrypt Mii QR codes from 3DS / Wii U / Miitomo
# Credit @jaames
from Crypto.Cipher import AES
from nintendo import miis
from nintendo.nex import common

from . import *

key = bytes([0x59, 0xFC, 0x81, 0x7E, 0x64, 0x46, 0xEA, 0x61, 0x90, 0x34, 0x7B, 0x20, 0xE9, 0xBD, 0xCE, 0x52])

class MiiData(miis.MiiData):
    def convert(self, stream): # @jaames
        nonce = stream.read(8)
        cipher = AES.new(key, AES.MODE_CCM, nonce + bytes([0, 0, 0, 0]))
        content = cipher.decrypt(stream.read(0x58))
        result = content[:12] + nonce + content[12:]

        return io.BytesIO(result)

    def mii_studio(self): # @HEYimHeroic
        studio_mii = [
            (8 if self.beard_color == 0 else self.beard_color),
            self.beard_type,
            self.fatness,
            self.eye_thickness,
            self.eye_color + 8,
            self.eye_rotation,
            self.eye_scale,
            self.eye_type,
            self.eye_distance,
            self.eye_height,
            self.eyebrow_thickness,
            (8 if self.eyebrow_color == 0 else self.eyebrow_color),
            self.eyebrow_rotation,
            self.eyebrow_scale,
            self.eyebrow_type,
            self.eyebrow_distance,
            self.eyebrow_height,
            self.face_color,
            self.blush_type,
            self.face_type,
            self.face_style,
            self.color,
            self.gender,
            (8 if self.glass_color == 0 else self.glass_color + 13 if self.glass_color < 6 else 0),
            self.glass_scale,
            self.glass_type,
            self.glass_height,
            (8 if self.hair_color == 0 else self.hair_color),
            self.hair_mirrored,
            self.hair_type,
            self.size,
            self.mole_scale,
            self.mole_enabled,
            self.mole_xpos,
            self.mole_ypos,
            self.mouth_thickness,
            (self.mouth_color + 19 if self.mouth_color < 4 else 0),
            self.mouth_scale,
            self.mouth_type,
            self.mouth_height,
            self.mustache_scale,
            self.mustache_type,
            self.mustache_height,
            self.nose_scale,
            self.nose_type,
            self.nose_height,
        ]

        n = 256
        mii_data = hexlify(pack('>B', 0))
        for v in studio_mii:
            eo = (7 + (v ^ n)) % 256
            n = eo
            mii_data += hexlify(pack('>B', eo))

        url = self.mii_studio_url(mii_data.decode('utf-8'))

        return url

    def mii_studio_url(self, mii_data):
        base = 'https://studio.mii.nintendo.com/miis/image.png?data=' + mii_data
        url = {
            'data': mii_data,
            'face': base + '&type=face&width=512&instanceCount=1',
            'body': base + '&type=all_body&width=512&instanceCount=1',
            'face-16x': base + '&type=face&width=512&instanceCount=16',
            'body-16x': base + '&type=all_body&width=512&instanceCount=16',
        }

        return url

class FriendInfo(common.Data):
	def __init__(self):
		super().__init__()
		self.unk1 = None
		self.unk2 = None

	def check_required(self, settings, version):
		for field in ['unk1', 'unk2']:
			if getattr(self, field) is None:
				raise ValueError(f"No value assigned to required field: {field}")

	def load(self, stream, version):
		self.unk1 = stream.u32()
		self.unk2 = stream.datetime()

	def save(self, stream, version):
		self.check_required(stream.settings, version)
		stream.u32(self.unk1)
		stream.datetime(self.unk2)
