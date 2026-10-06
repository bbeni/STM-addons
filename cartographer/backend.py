"""Connection to the motors: real Nanonis, or a simulator.

Intent (prompt, 2026-10-05): "the ui should be testable without nanonis for now".
Both classes offer the same few methods, so the UI does not know which one it
drives. The Nanonis calls are exactly the ones from nanonis_motor_controller.ipynb.

Tested on the STM (2026-10-06): "nanonis does ignore number of steps and
behaves weirdly. Fix: use np data types", e.g.
    Motor_StartMove(Direction=np.uint32(4), Number_of_steps=np.uint16(100),
                    Group=np.uint32(0), Wait_until_finished=np.uint16(True))
    Motor_FreqAmpSet(np.float32(1000), np.float32(200), np.uint16(0))
"last argument means all axes. should stay that way for all purposes".
So every argument is passed with its exact numpy type.
"""

import socket
import time

import numpy as np

NANONIS_DIRECTIONS = {"x+": 0, "x-": 1, "y+": 2, "y-": 3, "z+": 4, "z-": 5}
GROUP_DEFAULT = np.uint32(0)  # motor groups are not supported
AXIS_ALL = np.uint16(0)  # frequency and amplitude always for all axes
TRUE, FALSE = np.uint16(1), np.uint16(0)


class NanonisError(Exception):
    pass


class Nanonis:
    name = "Nanonis"

    def __init__(self, host, port, timeout_s=2):
        import nanonis_spm  # only needed with real hardware

        connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        connection.settimeout(timeout_s)
        connection.connect((host, port))
        self.nanonis = nanonis_spm.Nanonis(connection)

    def call(self, function, *args, **kwargs):
        # nanonis_spm returns (error message, raw response, values)
        error, _, values = getattr(self.nanonis, function)(*args, **kwargs)
        if error:
            raise NanonisError(f"{function}: {error}")
        return values

    def freq_amp(self):
        frequency_Hz, amplitude_V = self.call("Motor_FreqAmpGet", AXIS_ALL)
        # float32 from Nanonis: 190.3 arrives as 190.3000030517578
        return float(f"{frequency_Hz:.6g}"), float(f"{amplitude_V:.6g}")

    def start_move(self, direction, nsteps):
        self.call("Motor_StartMove", Direction=np.uint32(NANONIS_DIRECTIONS[direction]),
                  Number_of_steps=np.uint16(nsteps), Group=GROUP_DEFAULT, Wait_until_finished=FALSE)

    def stop_move(self):
        self.call("Motor_StopMove")

    def set_approach(self, on):
        self.call("AutoApproach_OnOffSet", On_Off=TRUE if on else FALSE)

    def approach_running(self):
        on_off, = self.call("AutoApproach_OnOffGet")
        return bool(on_off)


class Simulator:
    """Pretends to be Nanonis: moves do nothing, an approach takes a few seconds."""

    name = "Simulation"
    APPROACH_DURATION_s = 3.0

    def __init__(self):
        self.approach_end_s = None

    def freq_amp(self):
        return 1000.0, 200.0

    def start_move(self, direction, nsteps):
        pass

    def stop_move(self):
        pass

    def set_approach(self, on):
        self.approach_end_s = time.monotonic() + self.APPROACH_DURATION_s if on else None

    def approach_running(self):
        return self.approach_end_s is not None and time.monotonic() < self.approach_end_s
