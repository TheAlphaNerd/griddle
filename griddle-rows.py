#! /usr/bin/env python
# -*- Mode: Python; coding: utf-8; indent-tabs-mode: nil; tab-width: 4 -*-
#
#    Copyright (C) 2011 Artem Popov <artfwo@gmail.com>
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

# TODO: special-case led/all

import sys, time, socket, select, pybonjour, itertools
from OSC import OSCClient, OSCServer, OSCMessage, NoCallbackError

REGTYPE = '_monome-osc._udp'

DEFAULT_APP_HOST = 'localhost'
DEFAULT_APP_PORT = 8000
DEFAULT_APP_PREFIX = '/monome'
GRIDDLE_SERVICE_PREFIX = 'griddle'
GRIDDLE_PREFIX = '/griddle'

def fix_prefix(s):
    return '/%s' % s.strip('/')

class Waffle:
    def waffle_send_any(self, host, port, path, *args):
        msg = OSCMessage(path)
        map(msg.append, args)
        # FIXME: self.client is buggy
        # self.client.sendto(msg, (self.target_host, self.target_port), timeout=0)
        client = OSCClient()
        client.sendto(msg, (host, port), timeout=0)
    
    def waffle_send(self, path, *args):
        self.waffle_send_any(self.target_host, self.target_port, path, *args)
    
    def waffle_handler(self, addr, tags, data, client_address):
        if addr.startswith(self.target_prefix):
            if self.app_callback:
                self.app_callback(self.id, addr.replace(self.target_prefix, "", 1), tags, data)
        else:
			raise NoCallbackError(addr)

# TODO: unfocus on host 
# TODO: /sys/connect
class Monome(OSCServer, Waffle):
    def __init__(self, id, host, port):
        OSCServer.__init__(self, ('', 0))
        self.id = id
        self.focused = False
        self.target_host = host
        self.target_port = port
        self.target_prefix = GRIDDLE_PREFIX
        
        self.addMsgHandler('default', self.waffle_handler)
        self.addMsgHandler('/sys/connect', self.sys_misc)
        self.addMsgHandler('/sys/disconnect', self.sys_misc)
        self.addMsgHandler('/sys/id', self.sys_misc)
        self.addMsgHandler('/sys/size', self.sys_size)
        self.addMsgHandler('/sys/host', self.sys_host)
        self.addMsgHandler('/sys/port', self.sys_port)
        self.addMsgHandler('/sys/prefix', self.sys_prefix)
        self.addMsgHandler('/sys/rotation', self.sys_misc)
        
        self.waffle_send('/sys/host', 'localhost')
        self.waffle_send('/sys/port', self.server_address[1])
        self.waffle_send('/sys/info')
        
        self.app_callback = None
    
    def sys_misc(self, *args):
        pass
    
    def sys_size(self, addr, tags, data, client_address):
        self.xsize, self.ysize = data
    
    def sys_host(self, addr, tags, data, client_address):
        pass
    
    def sys_port(self, addr, tags, data, client_address):
        host, port = self.server_address
        if port == data[0]:
            self.focused = True
            self.waffle_send('/sys/prefix', GRIDDLE_PREFIX)
        else:
            self.focused = False
            print "lost focus (device changed port)"
    
    # prefix confirmation
    def sys_prefix(self, addr, tags, data, client_address):
        self.target_prefix = fix_prefix(data[0])

class Virtual(OSCServer, Waffle):
    def __init__(self, id, xsize, ysize, port=0):
        OSCServer.__init__(self, ('', port))
        self.id = id
        self.xsize = xsize
        self.ysize = ysize
        self.target_port = DEFAULT_APP_PORT
        self.target_host = DEFAULT_APP_HOST
        self.target_prefix = DEFAULT_APP_PREFIX
        
        self.addMsgHandler('default', self.waffle_handler)
        self.addMsgHandler('/sys/port', self.sys_port)
        self.addMsgHandler('/sys/host', self.sys_host)
        self.addMsgHandler('/sys/prefix', self.sys_prefix)

        self.addMsgHandler('/sys/connect', self.sys_misc)
        self.addMsgHandler('/sys/disconnect', self.sys_misc)
        self.addMsgHandler('/sys/rotation', self.sys_misc)
        
        self.addMsgHandler('/sys/info', self.sys_info)

        self.app_callback = None
    
    def sys_misc(self, addr, tags, data, client_address):
        #print "/sys message: %s %s" % (addr, data)
        pass

    def sys_port(self, addr, tags, data, client_address):
        self.waffle_send('/sys/port', self.target_port)
        self.target_port = data[0]
        self.waffle_send('/sys/port', self.target_port)
    
    def sys_host(self, addr, tags, data, client_address):
        self.waffle_send('/sys/host', self.target_host)
        self.target_host = data[0]
        self.waffle_send('/sys/host', self.target_host)
    
    def sys_prefix(self, addr, tags, data, client_address):
        self.target_prefix = fix_prefix(data[0])
        self.waffle_send('/sys/prefix', self.target_port)
    
    def sys_info(self, addr, tags, data, client_address):
        if len(data) == 2: host, port = data
        elif len(data) == 1: host, port = self.target_host, data[0]
        elif len(data) == 0: host, port = self.target_host, self.target_port
        else: return
        
        self.waffle_send_any(host, port, '/sys/id', self.id)
        self.waffle_send_any(host, port, '/sys/size', self.xsize, self.ysize)
        self.waffle_send_any(host, port, '/sys/host', self.target_host)
        self.waffle_send_any(host, port, '/sys/port', self.target_port)
        self.waffle_send_any(host, port, '/sys/prefix', self.target_prefix)
        self.waffle_send_any(host, port, '/sys/rotation', 0)

class MonomeWatcher:
    def __init__(self, app):
        self.app = app
        self.sdRef = pybonjour.DNSServiceBrowse(regtype=REGTYPE, callBack=self.browse_callback)
        self.resolved = []
    
    def resolve_callback(self, sdRef, flags, interfaceIndex, errorCode, fullname, hosttarget, port, txtRecord):
        self.resolved.append(True)
        self.resolved_host = hosttarget
        self.resolved_port = port
    
    def browse_callback(self, sdRef, flags, interfaceIndex, errorCode, serviceName, regtype, replyDomain):
        if errorCode != pybonjour.kDNSServiceErr_NoError:
            return

        # ignore our own stuff
        if serviceName.startswith(GRIDDLE_SERVICE_PREFIX):
            return

        # FIXME: IPV4 and IPv6 are separate services and are resolved twice
        if not (flags & pybonjour.kDNSServiceFlagsAdd):
            self.app.monome_removed(serviceName)
            return
        
        resolve_sdRef = pybonjour.DNSServiceResolve(0,
            interfaceIndex,
            serviceName,
            regtype,
            replyDomain,
            self.resolve_callback)
        
        try:
            while not self.resolved:
                ready = select.select([resolve_sdRef], [], [], 5)
                if resolve_sdRef not in ready[0]:
                    print 'Resolve timed out'
                    break
                pybonjour.DNSServiceProcessResult(resolve_sdRef)
            else:
                self.resolved.pop()
        finally:
            resolve_sdRef.close()
        
        self.app.monome_discovered(serviceName, self.resolved_host, self.resolved_port)

class Griddle:
    def __init__(self, config='griddle.conf'):
        self.devices    = {}
        self.services   = {}
        self.offsets    = {}
        self.transtbl   = {}
        self.watcher = MonomeWatcher(self)
        
        self.parse_config(config)
    
    def parse_config(self, filename):
        from ConfigParser import RawConfigParser
        config = RawConfigParser()
        config.read(filename)
        for s in config.sections():
            port = int(config.get(s, 'port'))
            config.remove_option(s, 'port')
            
            xsize, ysize = [int(d) for d in config.get(s, 'size').split(",")]
            config.remove_option(s, 'size')
            
            x_off, y_off = [int(d) for d in config.get(s, 'offset').split(",")]
            config.remove_option(s, 'offset')
            self.offsets[s] = (x_off, y_off)
            
            for device, offset in config.items(s):
                x_off, y_off = [int(d) for d in offset.split(",")]
                if self.offsets.has_key(device):
                    if (x_off, y_off) != self.offsets[device]:
                        raise RuntimeError("conflicting offsets for device %s" % device)
                self.offsets[device] = (x_off, y_off)
                
                if s in self.transtbl: self.transtbl[s].append(device)
                else: self.transtbl[s] = [device]
                if device in self.transtbl: self.transtbl[device].append(s)
                else: self.transtbl[device] = [s]
            
            self.add_virtual(s, xsize, ysize, port)
    
    def add_virtual(self, name, xsize, ysize, port=0):
        device = Virtual(name, xsize, ysize, port)
        self.devices[name] = device
        
        sphost, spport = device.server_address
        service_name = '%s-%s' % (GRIDDLE_SERVICE_PREFIX, name)
        self.services[name] = pybonjour.DNSServiceRegister(name=service_name,
            regtype=REGTYPE,
            port=port,
            callBack=None)
        print "creating %s (%d)" % (name, spport)
        device.app_callback = self.universal_callback

    def monome_discovered(self, serviceName, host, port):
        name = serviceName.split()[-1].strip('()') # take serial
        if not name in self.offsets: # only take affected devices
            return
        # FIXME: IPV4 and IPv6 are separate services and are resolved twice
        if not self.devices.has_key(name):
            # FIXME: assume localhost due to some local/real hostname weirdness
            monome = Monome(name, 'localhost', port)
            print "adding %s (%d)" % (name, port)
            self.devices[name] = monome
            self.devices[name].app_callback = self.universal_callback
    
    def monome_removed(self, name):
        # FIXME: IPV4 and IPv6 are separate services and are removed twice
        if self.devices.has_key(name):
            print "removing %s" % name
            self.devices[name].close()
            del self.devices[name]
        return
    
    def universal_callback(self, id, addr, tags, data):
        # where to send the message
        def find_region(x, y, devices):
            for d in devices:
                xoff, yoff = self.offsets[d]
                xsize, ysize = self.devices[d].xsize, self.devices[d].ysize
                if xoff <= x < xoff + xsize and yoff <= y < yoff + ysize:
                    return self.devices[d]
        
        if addr.endswith("grid/key"):
            x, y, args = data[0], data[1], data[2:]
            x_off, y_off = self.offsets[id]
            x, y = x + x_off, y + y_off
            to = find_region(x, y, [d for d in self.transtbl[id]])
            if to is not None:
                to.waffle_send('%s%s' % (to.target_prefix, addr), x, y, *args)
        elif addr.endswith("grid/led/set") or addr.endswith("grid/led/map"):
            x, y, args = data[0], data[1], data[2:]
            x_off, y_off = self.offsets[id]
            to = find_region(x, y, [d for d in self.transtbl[id]])
            x, y = x - x_off, y - y_off
            if to is not None:
                to.waffle_send('%s%s' % (to.target_prefix, addr), x, y, *args)
        elif addr.endswith("grid/led/row"):
            x, y, rows = data[0], data[1], data[2:]
            x_off, y_off = self.offsets[id]
            for row in rows:
                x, y = x + x_off, y + y_off
                to = find_region(x, y, [d for d in self.transtbl[id]])
                if to is not None:
                    to.waffle_send('%s%s' % (to.target_prefix, addr), x, y, row)
                x += 8
        else:
            for t in self.transtbl[id]:
                to = self.devices[t]
                to.waffle_send('%s%s' % (to.target_prefix, addr), *data)
        """
        for t in self.transtbl[id]:
            dev = self.devices[t]
            
            vx_off, vy_off = self.offsets[id]
            dx_off, dy_off = self.offsets[t]
            x_off = trsign(dev, self.devices[id]) * (vx_off + dx_off)
            y_off = trsign(dev, self.devices[id]) * (vy_off + dy_off)
            xsize, ysize = trsize(dev, self.devices[id])
            
            if addr.endswith("grid/key"):
                tr = translate_basic(data, -x_off, -y_off, xsize, ysize)
            elif addr.endswith("grid/led/set"):
                tr = translate_basic(data, x_off, y_off, xsize, ysize)
            elif addr.endswith("grid/led/row"):
                tr = translate_rowcol(data, x_off, y_off, xsize, ysize)
            elif addr.endswith("grid/led/col"):
                tr = translate_rowcol(data, x_off, y_off, xsize, ysize)
            elif addr.endswith("grid/led/map"):
                tr = translate_basic(data, x_off, y_off, xsize, ysize)
            else:
                tr = data
            
            if tr is not None:
                dev.waffle_send('%s%s' % (dev.target_prefix, addr), tr)
        """
    
    def run(self):
        while True:
            rlist = itertools.chain(self.devices.values(),
                self.services.values(),
                [self.watcher.sdRef])
            ready = select.select(rlist, [], [])
            for r in ready[0]:
                if isinstance(r, OSCServer):
                    r.handle_request()
                elif isinstance(r, pybonjour.DNSServiceRef):
                    pybonjour.DNSServiceProcessResult(r)
                else:
                    raise RuntimeError("unknown stuff in select: %s", r)
    
    def close(self):
        rlist = itertools.chain(self.devices.values(),
            self.services.values(),
            [self.watcher.sdRef])
        for s in rlist:
            s.close()

# autodetect splitting mode
def trsign(a, b):
    m = a if isinstance(a, Monome) else b
    v = a if isinstance(a, Virtual) else b
    if (m.xsize + m.ysize) > (v.xsize + v.ysize):
        return -1
    else:
        return 1

# key, led, map
def translate_basic(args, x_off, y_off, xsize, ysize):
    x, y, data = args[0], args[1], args[2:]
    x = x - x_off
    y = y - y_off
    if x in range(xsize) and y in range(ysize): return [x, y] + data
    else: return None

# row, col
def translate_rowcol(args, x_off, y_off, xsize, ysize):
    x, y, data = args[0], args[1], args[2:]
    x = x - x_off
    y = y - y_off
    while x < 0:
        x += 8
        if len(data) > 0: data.pop(0)
    data = data[:xsize / 8]
    if x in range(xsize) and y in range(ysize) and len(data)>0: return [x, y] + data
    else: return None

if len(sys.argv) > 1:
    config = sys.argv[1]
else:
    config = "griddle.conf"

app = Griddle(config)
try:
    app.run()
except KeyboardInterrupt:
    pass
finally:
    app.close()