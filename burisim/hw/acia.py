from collections import deque
import logging
import struct
import queue

from PySide import QtCore

_LOGGER = logging.getLogger(__name__)

class ACIA(object):
    """Emulation of 6551-style ACIA. Optionally pass a PySerial-compatible
    object which will be the serial port connected to the ACIA.

    """
    # Status register bits
    _ST_IRQ = 0b10000000
    _ST_TDRE = 0b00010000
    _ST_RDRF = 0b00001000

    def __init__(self):
        # Callbacks
        self.irq_cb = None

        self._listeners = []

        # Registers
        self._recv_data = 0
        self._status_reg = 0
        self._command_reg = 0
        self._control_reg = 0

        # Read queue
        self._input_queue = queue.Queue()

        # Hardware-reset
        self.hw_reset()

    @property
    def irq(self):
        return self._status_reg & ACIA._ST_IRQ != 0

    def register_listener(self, l):
        self._listeners.append(l)

    def receive_byte(self, b):
        """Called when the device has received a byte from the outside world."""
        self._input_queue.put(b)
        self.poll()

    def poll(self):
        """Call regularly to check for incoming data."""
        if self._status_reg & ACIA._ST_RDRF != 0:
            return

        try:
            next_byte = self._input_queue.get(False)
            self._recv_data = next_byte
            self._status_reg |= ACIA._ST_RDRF
            if self._command_reg & 0b1 == 0b1:
                self._trigger_irq()
        except queue.Empty:
            pass

    def hw_reset(self):
        """Perform a hardware reset."""
        self._status_reg = 0b00010000
        self._control_reg = 0b00000000
        self._command_reg = 0b00000000
        self._update_serial_port()

    def write_reg(self, reg_idx, value):
        """Write register using RS1 and RS0 as high and low bits indexing the
        register.

        """
        if reg_idx == 0:
            # Write transmit register
            self._tx(value)
        elif reg_idx == 1:
            # Programmed reset
            self._prog_reset()
        elif reg_idx == 2:
            # Write command reg.
            self._command_reg = value
            self._update_serial_port()
        elif reg_idx == 3:
            # Write control reg
            self._control_reg = value
            self._update_serial_port()
        else:
            raise IndexError('No such register: ' + repr(reg_idx))

    def read_reg(self, reg_idx):
        """Read register using RS1 and RS0 as high and low bits indexing the
        register.

        """
        self.poll()
        if reg_idx == 0:
            # Read receiver register
            self._status_reg &= ~(ACIA._ST_RDRF) # clear data reg full flag
            return self._recv_data
        elif reg_idx == 1:
            # Read status register clearing interrupt bit after the fact
            sr = self._status_reg
            self._status_reg &= 0b01111111

            # Raise IRQ line
            self._set_irq(True)
            return sr
        elif reg_idx == 2:
            # Read command reg.
            return self._command_reg
        elif reg_idx == 3:
            # Read control reg.
            return self._control_reg
        else:
            raise IndexError('No such register: ' + repr(reg_idx))

    def _set_irq(self, val):
        if self.irq_cb is not None:
            self.irq_cb(val)

    def _trigger_irq(self):
        """Trigger an interrupt."""
        self._status_reg |= 0b10010000
        self._set_irq(False)

    def _prog_reset(self):
        """Perform a programmed reset."""
        # NOTE: does not change control reg
        self._status_reg = 0b00010000
        self._command_reg = 0b00000000
        self._update_serial_port()

    def _tx(self, value):
        """Transmit byte."""
        # Ensure transmit data reg. is empty
        if self._status_reg & ACIA._ST_TDRE == 0:
            _LOGGER.warn('serial port overflow: dropping output.')
            return

        # Clear transmit data empty reg
        self._status_reg &= ~(ACIA._ST_TDRE)

        # Write output
        for l in self._listeners:
            l(value)

        # Set transmit data empty reg
        self._status_reg |= ACIA._ST_TDRE

        # Trigger IRQ if required
        tic = (self._command_reg >> 2) & 0b11
        if tic == 0b01:
            self._trigger_irq()

    def _update_serial_port(self):
        """Update associated serial port with new settings from control register."""

