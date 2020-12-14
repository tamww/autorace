# CODE IN THIS FILE IS GENERATED BY DONKEYCAR PROJECT
# https://github.com/autorope/donkeycar

from donkeycar.utils import *
import time
from donkeycar.parts.throttle_filter import ThrottleFilter
from donkeycar.parts.datastore import TubHandler
from donkeycar.parts.actuator import PCA9685, PWMSteering, PWMThrottle

def add_basic_modules(V, cfg):

    # return True when ai mode, otherwize respect user mode recording flag
    if cfg.RECORD_DURING_AI:
        V.add(AiRecordingCondition(), inputs=['user/mode', 'recording'], outputs=['recording'])

    # this throttle filter will allow one tap back for esc reverse
    th_filter = ThrottleFilter()
    V.add(th_filter, inputs=['user/throttle'], outputs=['user/throttle'])

    # add some other basic modules
    V.add(PilotCondition(), inputs=['user/mode'], outputs=['run_pilot'])
    rec_tracker_part = RecordTracker(cfg=cfg)
    V.add(rec_tracker_part, inputs=["tub/num_records"], outputs=[])
    V.add(AiRunCondition(), inputs=['user/mode'], outputs=['ai_running'])

    return V

def add_tub_save_data(V, cfg):
    inputs=['cam/image_array',
            'user/angle', 'user/throttle',
            'user/mode']

    types=['image_array',
           'float', 'float',
           'str']

    if cfg.RECORD_DURING_AI:
        inputs += ['pilot/angle', 'pilot/throttle']
        types += ['float', 'float']
    
    if cfg.CONTROL_NOISE:
        inputs += ['user/angle_noise', 'user/throttle_noise']
        types += ['float', 'float']

    inputs += ['angle', 'throttle']
    types += ['float', 'float']
    
    th = TubHandler(path=cfg.DATA_PATH)
    tub = th.new_tub_writer(inputs=inputs, types=types)
    V.add(tub, inputs=inputs, outputs=["tub/num_records"], run_condition='recording')

    return V, tub

def add_control_modules(V, cfg):
    steering_controller = PCA9685(cfg.STEERING_CHANNEL, cfg.PCA9685_I2C_ADDR, busnum=cfg.PCA9685_I2C_BUSNUM)
    steering = PWMSteering(controller=steering_controller,
                                    left_pulse=cfg.STEERING_LEFT_PWM,
                                    right_pulse=cfg.STEERING_RIGHT_PWM)

    throttle_controller = PCA9685(1, cfg.PCA9685_I2C_ADDR, busnum=cfg.PCA9685_I2C_BUSNUM)
    throttle = PWMThrottle(controller=throttle_controller)

    V.add(steering, inputs=['angle'], threaded=True)
    V.add(throttle, inputs=['throttle'], threaded=True)

    return V

#Choose what inputs should change the car.
class DriveMode:
    def __init__(self, cfg):
        self.cfg = cfg
    def run(self, mode,
                user_angle, user_throttle,
                pilot_angle, pilot_throttle):
        
        if self.cfg.CONTROL_NOISE:
            # only add noise if in user mode
            throttle_noise = 0
            angle_noise = 0

        if mode == 'user':
            # for quick reverse
            if user_throttle < 0 and user_throttle >= -0.3:
                user_throttle = user_throttle * 1.5
            
            if self.cfg.CONTROL_NOISE:
                # only apply noise it if user_throttle > 0
                if user_throttle > 0:
                    throttle_noise = round(random.uniform(-self.cfg.THROTTLE_NOISE,self.cfg.THROTTLE_NOISE),3) # 3 precision
                    angle_noise = round(random.uniform(-self.cfg.ANGLE_NOISE, self.cfg.ANGLE_NOISE),3)
                    
                    user_angle += angle_noise
                    user_throttle += throttle_noise

                    # THROTTLE BOUND
                    if user_throttle > 1.0:
                        user_throttle = 1.0
                    if user_throttle < 0.05:
                        user_throttle = 0.05
                    # STEERING ANGLE BOUND
                    if user_angle > 1.0:
                        user_angle = 1.0
                    if user_angle < -1.0:
                        user_angle = -1.0

                return user_angle, user_throttle, angle_noise, throttle_noise
            else:
                return user_angle, user_throttle

        elif mode == 'local_angle':
            if self.cfg.CONTROL_NOISE:
                return pilot_angle if pilot_angle else 0.0, user_throttle, angle_noise, throttle_noise
            else:
                return pilot_angle if pilot_angle else 0.0

        else:
            pilot_throttle = pilot_throttle * self.cfg.AI_THROTTLE_MULT if pilot_throttle else 0.0
            pilot_angle = pilot_angle if pilot_angle else 0.0
            # THROTTLE BOUND
            if pilot_throttle > self.cfg.AI_MAX_THROTTLE:
                pilot_throttle = self.cfg.AI_MAX_THROTTLE
            if pilot_throttle < self.cfg.AI_MIN_THROTTLE:
                pilot_throttle = self.cfg.AI_MIN_THROTTLE
            # STEER BOUND
            if pilot_angle > 1.0:
                pilot_angle = 1.0
            if pilot_angle < -1.0:
                pilot_angle = -1.0
            if self.cfg.CONTROL_NOISE:
                return pilot_angle, pilot_throttle,  angle_noise, throttle_noise
            else:
                return pilot_angle, pilot_throttle

class AiRunCondition:
    '''
    A bool part to let us know when ai is running.
    '''
    def run(self, mode):
        if mode == "user":
            return False
        return True

class AiRecordingCondition:
    '''
    return True when ai mode, otherwize respect user mode recording flag
    '''
    def run(self, mode, recording):
        if mode == 'user':
            return recording
        return True

def get_record_alert_color(num_records):
    col = (0, 0, 0)
    for count, color in cfg.RECORD_ALERT_COLOR_ARR:
        if num_records >= count:
            col = color
    return col

class RecordTracker:
    def __init__(self, cfg):
        self.cfg = cfg
        self.last_record_num = -100

    def run(self, num_records):
        if num_records is not None:
            if num_records % 10 == 0 and num_records != self.last_record_num:
                print("recorded", num_records, "records")
                self.last_record_num = num_records

#See if we should even run the pilot module.
#This is only needed because the part run_condition only accepts boolean
class PilotCondition:
    def run(self, mode):
        if mode == 'user':
            return False
        else:
            return True

class ImgPreProcess():
    '''
    preprocess camera image for inference.
    normalize and crop if needed.
    '''
    def __init__(self, cfg):
        self.cfg = cfg

    def run(self, img_arr):
        return normalize_and_crop(img_arr, self.cfg)