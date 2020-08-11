#!/usr/bin/env python3

from pathlib import Path
from sys import exit
from array import array
from signal import SIGINT
from struct import unpack
from threading import Thread

import gi
gi.require_version('GLib', '2.0')
from gi.repository import GLib

gi.require_version('Hinawa', '3.0')
from gi.repository import Hinawa

gi.require_version('Gtk', '3.0')
from gi.repository import Gtk

# query sound devices and get FireWire sound unit
snd_specific_types = {
    Hinawa.SndUnitType.DICE:        Hinawa.SndDice,
    Hinawa.SndUnitType.FIREWORKS:   Hinawa.SndEfw,
    Hinawa.SndUnitType.DIGI00X:     Hinawa.SndDg00x,
    Hinawa.SndUnitType.MOTU:        Hinawa.SndMotu,
    Hinawa.SndUnitType.TASCAM:      Hinawa.SndTscm,
}
for p in Path('/dev/snd/').glob('hw*'):
    fullpath = str(p)
    try:
        unit = Hinawa.SndUnit()
        unit.open(fullpath)
    except:
        del unit
        continue

    snd_type = unit.get_property('type')
    if snd_type in snd_specific_types:
        del unit
        unit = snd_specific_types[snd_type]()
        try:
            unit.open(fullpath)
        except:
            del unit
            continue
    break

if 'unit' not in locals():
    print('No sound FireWire devices found.')
    exit()

# Setup sound unit and start a thread to dispatch events from the sound unit.
unit_ctx = GLib.MainContext.new()
unit_src = unit.create_source()
unit_src.attach(unit_ctx)
unit_dispatcher = GLib.MainLoop.new(unit_ctx, False)

def handle_lock_status(unit, status):
    if status:
        print("streaming is locked.");
    else:
        print("streaming is unlocked.");
def handle_unit_disconnected(unit, unit_dispatcher):
    print('unit disconnected')
    unit_dispatcher.quit()
unit.connect("lock-status", handle_lock_status)
unit.connect("disconnected", handle_unit_disconnected, unit_dispatcher)

unit_th = Thread(target=lambda d: d.run(), args=(unit_dispatcher,))
unit_th.start()

print('Sound device info:')
print(' type:\t\t{0}'.format(unit.get_property("type").value_nick))
print(' card:\t\t{0}'.format(unit.get_property("card")))
print(' device:\t{0}'.format(unit.get_property("device")))
print(' GUID:\t\t{0:016x}'.format(unit.get_property("guid")))

# Setup firewire node and start a thread to dispatch events from the node.
node = unit.get_node()
node_ctx = GLib.MainContext.new()
node_src = node.create_source()
node_src.attach(node_ctx)
node_dispatcher = GLib.MainLoop.new(node_ctx, False)

def handle_bus_update(node):
    print('bus-reset: generation {0}'.format(node.get_property('generation')))
def handle_node_disconnected(node, node_dispatcher):
    print('node disconnected')
    node_dispatcher.quit()
    Gtk.main_quit()
node.connect("bus-update", handle_bus_update)
node.connect('disconnected', handle_node_disconnected, node_dispatcher)

node_th = Thread(target=lambda d: d.run(), args=(node_dispatcher,))
node_th.start()

print('\nIEEE1394 node info:')
print(' Node IDs:')
print('  self:\t\t{:04x}'.format(node.get_property('node-id')))
print('  local:\t{:04x}'.format(node.get_property('local-node-id')))
print('  root:\t\t{:04x}'.format(node.get_property('root-node-id')))
print('  bus-manager:\t{:04x}'.format(node.get_property('bus-manager-node-id')))
print('  ir-manager:\t{:04x}'.format(node.get_property('ir-manager-node-id')))
print('  generation:\t{}'.format(node.get_property('generation')))
print(' Config ROM:')
config_rom = node.get_config_rom()
quads = unpack('>{}I'.format(len(config_rom) // 4), config_rom)
for i, q in enumerate(quads):
    print('  0xfffff00004{:02x}: {:08x}'.format(i * 4, q))

# create firewire responder
resp = Hinawa.FwResp()
def handle_request(resp, tcode):
    print('Requested with tcode: {0}'.format(tcode.value_nick))
    req_frame = resp.get_req_frame()
    for i in range(len(req_frame)):
        print(' [{0:02d}]: 0x{1:02x}'.format(i, req_frame[i]))
    return Hinawa.FwRcode.COMPLETE
try:
    resp.reserve(node, 0xfffff0000d00, 0x100)
    resp.connect('requested', handle_request)
except Exception as e:
    print(e)
    exit()

# create firewire requester
req = Hinawa.FwReq()

# Fireworks/BeBoB/OXFW supports FCP and some AV/C commands
fcp_types = (
    Hinawa.SndUnitType.FIREWORKS,
    Hinawa.SndUnitType.BEBOB,
    Hinawa.SndUnitType.OXFW,
)
if unit.get_property('type') in fcp_types:
    fcp = Hinawa.FwFcp()
    try:
        fcp.bind(node)
    except Exception as e:
        print(e)
        exit()
    request = bytes([0x01, 0xff, 0x19, 0x00, 0xff, 0xff, 0xff, 0xff])
    response = bytearray(8)
    try:
        response = fcp.transaction(request, response)
    except Exception as e:
        print(e)
        exit()
    print('FCP Response:')
    for i, b in enumerate(response):
        print(' [{0:02d}]: 0x{1:02x}'.format(i, b))
    fcp.unbind()
    del fcp

# Echo Fireworks Transaction
if unit.get_property('type') is Hinawa.SndUnitType.FIREWORKS:
    args = array('I')
    args.append(5)
    params = array('I')
    params.append(0xffffffff)
    params.append(0xffffffff)
    try:
        params = unit.transaction(6, 1, args, params)
    except Exception as e:
        print(e)
        exit()
    print('Echo Fireworks Transaction Response:')
    for i in range(len(params)):
        print(" [{0:02d}]: {1:08x}".format(i, params[i]))

# Dice notification
def handle_notification(self, message):
    print("Dice Notification: {0:08x}".format(message))
if unit.get_property('type') is Hinawa.SndUnitType.DICE:
    unit.connect('notified', handle_notification)
    args = array('I')
    args.append(0x0000030c)
    try:
        # The address of clock in Impact Twin
        unit.transaction(0xffffe0000074, args, 0x00000020)
    except Exception as e:
        print(e)
        exit()

# Dg00x message
def handle_message(self, message):
    print("Dg00x Messaging {0:08x}".format(message));
if unit.get_property('type') is Hinawa.SndUnitType.DIGI00X:
    print('hear message')
    unit.connect('message', handle_message)

# Motu notification and status
def handle_motu_notification(self, message):
    print("Motu Notification: {0:08x}".format(message))
if unit.get_property('type') is Hinawa.SndUnitType.MOTU:
    unit.connect('notified', handle_motu_notification)

# Tascam control
def handle_tscm_control(self, index, before, after):
    print('control:{}: {:08x}'.format(index, before ^ after))
if unit.get_property('type') is Hinawa.SndUnitType.TASCAM:
    unit.connect('control', handle_tscm_control)

# GUI
class Sample(Gtk.Window):

    def __init__(self):
        Gtk.Window.__init__(self, title="Hinawa-2.0 gir sample with Gtk+3")
        self.set_border_width(20)

        grid = Gtk.Grid(row_spacing=10, row_homogeneous=True,
                        column_spacing=10, column_homogeneous=True)
        self.add(grid)

        button = Gtk.Button(label="transact")
        button.connect("clicked", self.on_click_transact)
        grid.attach(button, 0, 0, 1, 1)

        button = Gtk.Button(label="_Close", use_underline=True)
        button.connect("clicked", self.on_click_close)
        grid.attach(button, 1, 0, 1, 1)

        self.entry = Gtk.Entry()
        self.entry.set_text("0xfffff0000980")
        grid.attach(self.entry, 0, 1, 1, 1)

        self.label = Gtk.Label(label="result")
        self.label.set_text("0x00000000")
        grid.attach(self.label, 1, 1, 1, 1)

        # handle unix signal
        GLib.unix_signal_add(GLib.PRIORITY_HIGH, SIGINT,
                             self.handle_unix_signal, None)

    def handle_unix_signal(self, user_data):
        self.on_click_close( None)

    def on_click_transact(self, button):
        try:
            addr = int(self.entry.get_text(), 16)
            frames = bytearray(4);
            frames = req.transaction(node, Hinawa.FwTcode.READ_QUADLET_REQUEST,
                                     addr, 4, frames)
        except Exception as e:
            print(e)
            return

        label = '0x{:08x}'.format(unpack('>I', frames)[0])
        self.label.set_text(label)
        print(self.label.get_text())

    def on_click_close(self, button):
        Gtk.main_quit()

# Main logic
win = Sample()
win.connect("delete-event", Gtk.main_quit)
win.show_all()

Gtk.main()

del win
print('delete window object')

unit_dispatcher.quit()
node_dispatcher.quit()
unit_th.join()
node_th.join()

resp.release()
del resp
print('delete fw_resp object')

del req
print('delete fw_req object')

del node
del unit

exit()
