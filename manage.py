#!/usr/bin/env python3
"""
Scripts to drive a donkey 2 car

Usage:
    manage.py (drive) [--model=<model>] [--js] [--type=(linear|rnn|resnet18|resnet18_imu)] [--myconfig=<filename>] [--trt] [--half]
    manage.py (train) [--tub=<tub1,tub2,..tubn>] [--file=<file> ...] (--model=<model>) [--type=(linear|rnn|resnet18|resnet18_imu)] [--continuous] [--aug] [--myconfig=<filename>] [--pretrain=<pretrain_model>]


Options:
    -h --help               Show this screen.
    --js                    Use physical joystick.
    -f --file=<file>        A text file containing paths to tub files, one per line. Option may be used more than once.
    --myconfig=filename     Specify myconfig file to use. 
                            [default: myconfig.py]
"""
import os
import time
from docopt import docopt
import numpy as np
import donkeycar as dk
from donkeycar.parts.controller import LocalWebController, JoystickController, WebFpv
# from donkeycar.utils import *
from tools import *
from donkeycar.parts.camera import CSICamera
from ai_drive_models import LinearModel, DriveClass, DriveIMUClass, RNNModel, LinearResModel, LinearResIMUModel
from uwb_tools import UWBClass
            
def drive(cfg, model_path=None, use_joystick=False, use_trt = False, use_half = False, model_type=None):

    # -------------------------------- 
    # 1. Initialize car
    # --------------------------------
    V = dk.vehicle.Vehicle()


    # -------------------------------- 
    # 2. Add camera
    # -------------------------------- 
    cam = CSICamera(image_w=cfg.IMAGE_W, image_h=cfg.IMAGE_H, framerate=cfg.CAMERA_FRAMERATE, crop_top=cfg.ROI_CROP_TOP, crop_bottom=cfg.ROI_CROP_BOTTOM)
    V.add(cam, inputs=[], outputs=['cam/image_array'], threaded=True)
    if cfg.USE_FPV:
        V.add(WebFpv(), inputs=['cam/image_array'], threaded=True) # send the FPV image through network at port 8890

    # -------------------------------- 
    # 2.5 Add UWB function, which can provide:
    #     -- velocity: v_x, v_y, v_z
    #     -- angular velocity: av_x, av_y, av_z
    #     -- acceleration: a_x, a_y, a_z
    #     << -------------------------------- >> THE FOLLOWING MAY NOT BE USED, BUT WE STILL RECORD THEM.
    #     -- position: x, y, z
    #     -- orientation: o_x, o_y, o_z
    #     -- pose uncertainty: u_x, u_y, u_z
    # -------------------------------- 

    if cfg.HAVE_UWB:
        uwb = UWBClass(cfg, cam)
        V.add(uwb, inputs=[], outputs=[
            'imu/acl_x', 'imu/acl_y', 'imu/acl_z', # m/s^2
            'imu/gyr_x', 'imu/gyr_y', 'imu/gyr_z', # rad/s
            'uwb/vel_x', 'uwb/vel_y', 'uwb/vel_z', # m/s
            'uwb/pose_x', 'uwb/pose_y', 'uwb/pose_z', # m 
            'uwb/pose_unc_x', 'uwb/pose_unc_y', 'uwb/pose_unc_z', #m
            'uwb/ori_x', 'uwb/ori_y', 'uwb/ori_z', 'uwb/tag_id', 'uwb/voltage'], threaded=True) #degree, None, V


    # -------------------------------- 
    # 3. Add gamepad or webpage controller
    # --------------------------------
    if model_path:
        cfg.WEB_INIT_MODE = "local"
    if use_joystick or cfg.USE_JOYSTICK_AS_DEFAULT:
        from donkeycar.parts.controller import get_js_controller
        ctr = get_js_controller(cfg)
        V.add(ctr, 
          inputs=['cam/image_array'],
          outputs=['user/angle', 'user/throttle', 'user/mode', 'recording'],
          threaded=True)
    else:
        # This web controller will create a web server that is capable of managing steering, throttle, and modes, and more.
        ctr = LocalWebController(port=cfg.WEB_CONTROL_PORT, mode=cfg.WEB_INIT_MODE)
        V.add(ctr,
          inputs=['cam/image_array', 'tub/num_records'],
          outputs=['user/angle', 'user/throttle', 'user/mode', 'recording'],
          threaded=True)
    V = add_basic_modules(V, cfg) # e.g., record tracker
    

    # -------------------------------- 
    # 4. Configure the AI neural network model
    # --------------------------------
    model_inputs = ['cam/image_array']
    if 'imu' in model_type:
        model_inputs.extend([
            'imu/acl_x', 'imu/acl_y', 'imu/acl_z',
            'imu/gyr_x', 'imu/gyr_y', 'imu/gyr_z',
            'uwb/vel_x', 'uwb/vel_y'])
    if model_path:
        print('loading the self-driving model, model_path:', model_path)
        t0 = time.time()
        import torch
        device = torch.device('cuda')
        if not use_trt:
            if model_type == 'linear':
                drive_model = LinearModel().to(device)
            elif model_type == 'resnet18':
                drive_model = LinearResModel().to(device)
            elif model_type == 'rnn':
                drive_model = RNNModel().to(device)
            elif model_type == 'resnet18_imu':
                drive_model = LinearResIMUModel().to(device)
            drive_model.load_state_dict(torch.load(model_path,map_location=lambda storage, loc: storage))
            if use_half:
                drive_model.eval().half()
            else:
                drive_model.eval()
        else:
            from torch2trt import TRTModule
            drive_model = TRTModule()
            drive_model.load_state_dict(torch.load(model_path)) # no need to move to device if using torch2trt
        print('model loaded, time cost: %.2f s'%(time.time()-t0))
        
        if 'imu' not in model_type:
            drive_class = DriveClass(cfg, model_type, drive_model, device, cam = cam, half = use_half)
        else:
            drive_class = DriveIMUClass(cfg, model_type, drive_model, device, cam = cam, half = use_half)
        
        outputs=['pilot/angle', 'pilot/throttle']
        V.add(drive_class, inputs=model_inputs,
            outputs=outputs,
            run_condition='run_pilot', threaded=False)
            # if use threaded mode, the input image will not be used, 
            # because the thread will contineously read images from vehicle memory.

    # -------------------------------- 
    # 5. Choose which control command to use
    # -------------------------------- 
    V.add(DriveMode(cfg),
          inputs=['user/mode', 'user/angle', 'user/throttle',
                  'pilot/angle', 'pilot/throttle'],
          outputs=['angle', 'throttle'])


    # -------------------------------- 
    # 6. Configure the low-level controller that translates steering and throttle to PWM signals
    # -------------------------------- 
    V = add_control_modules(V, cfg)


    # -------------------------------- 
    # 7. Add tub to save data
    # -------------------------------- 
    V, tub = add_tub_save_data(V, cfg)


    # -------------------------------- 
    # 8. Print user guide
    # -------------------------------- 
    if type(ctr) is LocalWebController:
        print("You can now go to <your hostname.local>:%d to drive your car." % cfg.WEB_CONTROL_PORT)
    elif isinstance(ctr, JoystickController):
        print("You can now move your joystick to drive your car.")
        #tell the controller about the tub
        ctr.set_tub(tub)
        ctr.print_controls()

    # --------------------------------
    # 9. press "Enter" to start if using AI mode, or else directly start the vehicle
    # --------------------------------
    if model_path:
        enter = input("press ENTER to start racing")
        if enter == '':
            V.start(rate_hz=cfg.DRIVE_LOOP_HZ, max_loop_count=cfg.MAX_LOOPS) #run the vehicle for DRIVE_LOOP_HZ, e.g., 20 HZ
    else:
        V.start(rate_hz=cfg.DRIVE_LOOP_HZ, max_loop_count=cfg.MAX_LOOPS)

if __name__ == '__main__':
    args = docopt(__doc__)
    cfg = dk.load_config(myconfig=args['--myconfig'])

    model_type = args['--type']
    if model_type is None:
        model_type = cfg.DEFAULT_MODEL_TYPE
        print("using default model type of", model_type)

    if args['drive']:
        trt = args['--trt']

        drive(cfg, model_path=args['--model'], use_joystick=args['--js'], use_trt = trt, use_half = args['--half'],
              model_type=model_type)

    if args['train']:
        from train import multi_train, preprocessFileList

        tub = args['--tub']
        model = args['--model']
        pretrain = args['--pretrain']
        dirs = preprocessFileList(args['--file'])

        if tub is not None:
            tub_paths = [os.path.expanduser(n) for n in tub.split(',')]
            dirs.extend( tub_paths )

        multi_train(cfg, dirs, model, model_type, pretrain)

