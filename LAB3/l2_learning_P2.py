# Copyright 2011-2012 James McCauley
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at:
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
An L2 learning switch.

It is derived from one written live for an SDN crash course.
It is somwhat similar to NOX's pyswitch in that it installs
exact-match rules for each flow.
"""

from pox.core import core
import pox.openflow.libopenflow_01 as of
from pox.lib.util import dpid_to_str, str_to_dpid
from pox.lib.util import str_to_bool
import time

#ruta_valida verifica que no se puedan comunicar entre hosts
#Como h5 y h6 son servers, siempre responderan al host que envio el frame, asi que no es necesario bloquear h5 <-> h6; ni h5 -> h3/h4, h6 -> h1/h2 porque mas adelante se filtran, pero igual lo anotaremos
def ruta_valida(mac_src, mac_dst):
  if ( (mac_src == '1' or mac_src == '2') and mac_dst == '5' ):
    return True
  elif ( (mac_src == '3' or mac_src == '4') and mac_dst == '6' ):
    return True
  elif ( mac_src == '5' and (mac_dst == '1' or mac_dst == '2') ):
    return True
  elif ( mac_src == '6' and (mac_dst == '3' or mac_dst == '4') ):
    return True
  else:
    return False

log = core.getLogger()

# We don't want to flood immediately when a switch connects.
# Can be overriden on commandline.
_flood_delay = 0

class LearningSwitch (object):
  """
  The learning switch "brain" associated with a single OpenFlow switch.

  When we see a packet, we'd like to output it on a port which will
  eventually lead to the destination.  To accomplish this, we build a
  table that maps addresses to ports.

  We populate the table by observing traffic.  When we see a packet
  from some source coming from some port, we know that source is out
  that port.

  When we want to forward traffic, we look up the desintation in our
  table.  If we don't know the port, we simply send the message out
  all ports except the one it came in on.  (In the presence of loops,
  this is bad!).

  In short, our algorithm looks like this:

  For each packet from the switch:
  1) Use source address and switch port to update address/port table
  2) Is transparent = False and either Ethertype is LLDP or the packet's
     destination address is a Bridge Filtered address?
     Yes:
        2a) Drop packet -- don't forward link-local traffic (LLDP, 802.1x)
            DONE
  3) Is destination multicast?
     Yes:
        3a) Flood the packet
            DONE
  4) Port for destination address in our address/port table?
     No:
        4a) Flood the packet
            DONE
  5) Is output port the same as input port?
     Yes:
        5a) Drop packet and similar ones for a while
  6) Install flow table entry in the switch so that this
     flow goes out the appopriate port
     6a) Send the packet out appropriate port
  """
  def __init__ (self, connection, transparent):
    # Switch we'll be adding L2 learning switch capabilities to
    self.connection = connection
    self.transparent = transparent

    # Our table
    self.macToPort = {}

    # We want to hear PacketIn messages, so we listen
    # to the connection
    connection.addListeners(self)

    # We just use this to know when to log a helpful message
    self.hold_down_expired = _flood_delay == 0

    #log.debug("Initializing LearningSwitch, transparent=%s",
    #          str(self.transparent))

  def _handle_PacketIn (self, event):
    """
    Handle packet in messages from the switch to implement above algorithm.
    """

    packet = event.parsed

    def flood (message = None):
      """ Floods the packet """
      msg = of.ofp_packet_out()
      if time.time() - self.connection.connect_time >= _flood_delay:
        # Only flood if we've been connected for a little while...

        if self.hold_down_expired is False:
          # Oh yes it is!
          self.hold_down_expired = True
          log.info("%s: Flood hold-down expired -- flooding",
              dpid_to_str(event.dpid))

        if message is not None: log.debug(message)
        #log.debug("%i: flood %s -> %s", event.dpid,packet.src,packet.dst)
        # OFPP_FLOOD is optional; on some switches you may need to change
        # this to OFPP_ALL.
        msg.actions.append(of.ofp_action_output(port = of.OFPP_FLOOD))
      else:
        pass
        #log.info("Holding down flood for %s", dpid_to_str(event.dpid))
      msg.data = event.ofp
      msg.in_port = event.port
      self.connection.send(msg)

    def drop (duration = None):
      """
      Drops this packet and optionally installs a flow to continue
      dropping similar ones for a while
      """
      if duration is not None:
        if not isinstance(duration, tuple):
          duration = (duration,duration)
        msg = of.ofp_flow_mod()
        msg.match = of.ofp_match.from_packet(packet)
        msg.idle_timeout = duration[0]
        msg.hard_timeout = duration[1]
        msg.buffer_id = event.ofp.buffer_id
        self.connection.send(msg)
      elif event.ofp.buffer_id is not None:
        msg = of.ofp_packet_out()
        msg.buffer_id = event.ofp.buffer_id
        msg.in_port = event.port
        self.connection.send(msg)

    self.macToPort[packet.src] = event.port # 1

    if not self.transparent: # 2
      if packet.type == packet.LLDP_TYPE or packet.dst.isBridgeFiltered():
        drop() # 2a
        return

    if packet.dst.is_multicast:
      flood() # 3a
    else:
      if packet.dst not in self.macToPort: # 4
        flood("Port for %s unknown -- flooding" % (packet.dst,)) # 4a
      else:
        '''
        port = self.macToPort[packet.dst]
        if port == event.port: # 5
          # 5a
          log.warning("Same port for packet from %s -> %s on %s.%s.  Drop."
              % (packet.src, packet.dst, dpid_to_str(event.dpid), port))
          drop(10)
          return
        # 6
        log.debug("installing flow for %s.%i -> %s.%i" %
                  (packet.src, event.port, packet.dst, port))
        '''
        msg = of.ofp_flow_mod()
        msg.match = of.ofp_match.from_packet(packet, event.port)
        msg.idle_timeout = 10
        msg.hard_timeout = 30
        
        #Capturar datos del mensaje
        mac_src = str(packet.src)
        mac_dst = str(packet.dst)
        puerto = event.port
        
        print("_________________________________")
        print("MAC src: ", mac_src)
        print("MAC dst: ", mac_dst)
        print("Puerto de llegada: ", puerto)

        #Verificar que los protocolos sean los permitidos
        if packet.find('arp') or packet.find('tcp'):

          #Solo permitir llamados hacia los servidores y respuestas hacia los hosts
          #h1 -> h5   //    h5 -> h1
          #h2 -> h5   //    h5 -> h2
          #h3 -> h6   //    h6 -> h3
          #h4 -> h6   //    h6 -> h4

          if ( ruta_valida(mac_src[-1], mac_dst[-1]) ):
            #Redirigir el flujo de salida segun el host que envia el mensaje
            #Host 1 y 2
            if mac_src[-1] == '1' or mac_src[-1] == '2':
              #Salen por el puerto 17
              if puerto == 2 or puerto == 4:
                print("Puerto de salida: ", 17)
                msg.actions.append(of.ofp_action_output(port = 17))

            #Host 3 y 4
            if mac_src[-1] == '3' or mac_src[-1] == '4':
              #Salen por el puerto 15
              if puerto == 6 or puerto == 8:
                print("Puerto de salida: ", 15)
                msg.actions.append(of.ofp_action_output(port = 15))

            #Host 5 y 6
            if mac_src[-1] == '5' or mac_src[-1] == '6':
              #Salen por el puerto 19
              if puerto == 10 or puerto == 12:
                print("Puerto de salida: ", 19)
                msg.actions.append(of.ofp_action_output(port = 19))

            #Redirigir el flujo de los switches
            #Switch 1
            #Si viene desde el host 3 o 4, sigue el flujo, puerto 17
            if puerto == 16:
              if mac_src[-1] == '3' or mac_src[-1] == '4':
                print("Puerto de salida: ", 17)
                msg.actions.append(of.ofp_action_output(port = 17))

            #Switch 3
            if puerto == 20:
              #Si la respuesta va al host 1 o 2, pasa al puerto 21
              if mac_dst[-1] == '1' or mac_dst[-1] == '2':
                print("Puerto de salida: ", 21)
                msg.actions.append(of.ofp_action_output(port = 21))
              
              #En otro caso, sigue el flujo de los switches, puerto 23
              else:
                print("Puerto de salida: ", 23)
                msg.actions.append(of.ofp_action_output(port = 23))

            #Switch 4
            if puerto == 24:
              #Todas las respuestas siguen el flujo, puerto 13
              print("Puerto de salida: ", 13)
              msg.actions.append(of.ofp_action_output(port = 13))

            #Redirigir el flujo de llegada a los hosts a travas de los switches            
            #Switch 1
            if puerto == 22:
              #Si la respuesta va al host 1, usa el puerto 2
              if mac_dst[-1] == '1':
                print("Puerto de salida: ", 2)
                msg.actions.append(of.ofp_action_output(port = 2))

              #Si la respuesta va al host 2, usa el puerto 4
              elif mac_dst[-1] == '2':
                print("Puerto de salida: ", 4)
                msg.actions.append(of.ofp_action_output(port = 4))

            #Switch 2
            if puerto == 14:
              #Si la respuesta va al host 3, usa el puerto 6
              if mac_dst[-1] == '3':
                print("Puerto de salida: ", 6)
                msg.actions.append(of.ofp_action_output(port = 6))
              
              #Si la respuesta va al host 4, usa el puerto 8
              elif mac_dst[-1] == '4':
                print("Puerto de salida: ", 8)
                msg.actions.append(of.ofp_action_output(port = 8))

            #Switch 5
            if puerto == 18:
              #Si el mensaje venia desde host 1 o 2, solo acepta al host 5, puerto 10
              if (mac_src[-1] == '1' or mac_src[-1] == '2') and mac_dst[-1] == '5':
                print("Puerto de salida: ", 10)
                msg.actions.append(of.ofp_action_output(port = 10))

              #Si el mensaje venia desde host 3 o 4, solo acepta al host 6, puerto 12
              elif (mac_src[-1] == '3' or mac_src[-1] == '4') and mac_dst[-1] == '6':
                print("Puerto de salida: ", 12)
                msg.actions.append(of.ofp_action_output(port = 12))
              
              #En otros casos, se rechaza
              else:
                print("No se permite la comunicacion entre los host 1/2 con el servidor 6 ni entre los hosts 3/4 con el servidor 5")
                drop(1)          

            msg.data = event.ofp # 6a
            self.connection.send(msg)
          
          else:
            if ( (mac_src[-1] == '1' or mac_src[-1] == '2') and mac_dst[-1] == '6' ) or ( mac_src[-1] == '6' and (mac_dst[-1] == '1' or mac_dst[-1] == '2') ):
              print("No se permite la comunicacion entre los host 1/2 con el servidor 6")
            
            elif ( (mac_src[-1] == '3' or mac_src[-1] == '4') and mac_dst[-1] == '5' ) or ( mac_src[-1] == '5' and (mac_dst[-1] == '3' or mac_dst[-1] == '4') ):
              print("No se permite la comunicacion entre los host 3/4 con el servidor 5")

            else:
              print("No se permite la comunicacion entre hosts")
            drop(1)

        else:
          print("No se permite conexion no HTTP")
          drop(1)


class l2_learning (object):
  """
  Waits for OpenFlow switches to connect and makes them learning switches.
  """
  def __init__ (self, transparent, ignore = None):
    """
    Initialize

    See LearningSwitch for meaning of 'transparent'
    'ignore' is an optional list/set of DPIDs to ignore
    """
    core.openflow.addListeners(self)
    self.transparent = transparent
    self.ignore = set(ignore) if ignore else ()

  def _handle_ConnectionUp (self, event):
    if event.dpid in self.ignore:
      log.debug("Ignoring connection %s" % (event.connection,))
      return
    log.debug("Connection %s" % (event.connection,))
    LearningSwitch(event.connection, self.transparent)


def launch (transparent=False, hold_down=_flood_delay, ignore = None):
  """
  Starts an L2 learning switch.
  """
  try:
    global _flood_delay
    _flood_delay = int(str(hold_down), 10)
    assert _flood_delay >= 0
  except:
    raise RuntimeError("Expected hold-down to be a number")

  if ignore:
    ignore = ignore.replace(',', ' ').split()
    ignore = set(str_to_dpid(dpid) for dpid in ignore)

  core.registerNew(l2_learning, str_to_bool(transparent), ignore)
