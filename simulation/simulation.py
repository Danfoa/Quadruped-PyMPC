# Description: This script is used to simulate the full model of the robot in mujoco

# Authors:
# Giulio Turrisi, Daniel Ordonez

import time
import numpy as np
from tqdm import tqdm
# Gym and Simulation related imports
from gym_quadruped.quadruped_env import QuadrupedEnv
from gym_quadruped.utils.mujoco.visual import render_vector
from gym_quadruped.utils.quadruped_utils import LegsAttr
# Control imports
from quadruped_pympc import config as cfg

from quadruped_pympc.helpers.quadruped_utils import plot_swing_mujoco

from quadruped_pympc.srbd_controller_interface import SRBDControllerInterface
from quadruped_pympc.srbd_batched_controller_interface import SRBDBatchedControllerInterface
from quadruped_pympc.wb_interface import WBInterface


# HeightMap import
if(cfg.simulation_params['visual_foothold_adaptation'] != 'blind'):
    from gym_quadruped.sensors.heightmap import HeightMap

from gym_quadruped.utils.mujoco.visual import render_sphere




if __name__ == '__main__':
    np.set_printoptions(precision=3, suppress=True)

    robot_name = cfg.robot
    hip_height = cfg.hip_height
    robot_leg_joints = cfg.robot_leg_joints
    robot_feet_geom_names = cfg.robot_feet_geom_names
    scene_name = cfg.simulation_params['scene']
    simulation_dt = cfg.simulation_params['dt']

    state_observables_names = ('base_pos', 'base_lin_vel', 'base_ori_euler_xyz', 'base_ori_quat_wxyz', 'base_ang_vel',
                               'qpos_js', 'qvel_js', 'tau_ctrl_setpoint',
                               'feet_pos_base', 'feet_vel_base', 'contact_state', 'contact_forces_base',)


    # Create the quadruped robot environment -----------------------------------------------------------
    env = QuadrupedEnv(robot=robot_name,
                       hip_height=hip_height,
                       legs_joint_names=robot_leg_joints,  # Joint names of the legs DoF
                       feet_geom_name=robot_feet_geom_names,  # Geom/Frame id of feet
                       scene=scene_name,
                       sim_dt=simulation_dt,
                       ref_base_lin_vel=0.0,  # pass a float for a fixed value
                       ground_friction_coeff=1.5,  # pass a float for a fixed value
                       base_vel_command_type="human",  # "forward", "random", "forward+rotate", "human"
                       state_obs_names=state_observables_names,  # Desired quantities in the 'state' vec
                       )
    # env = QuadrupedEnv(robot=robot_name,
    #                    hip_height=hip_height,
    #                    legs_joint_names=LegsAttr(**robot_leg_joints),  # Joint names of the legs DoF
    #                    feet_geom_name=LegsAttr(**robot_feet_geom_names),  # Geom/Frame id of feet
    #                    scene=scene_name,
    #                    sim_dt=simulation_dt,
    #                    ref_base_lin_vel=(-4.0 * hip_height, 4.0 * hip_height),  # pass a float for a fixed value
    #                    ref_base_ang_vel=(-np.pi * 3 / 4, np.pi * 3 / 4),  # pass a float for a fixed value
    #                    ground_friction_coeff=(0.3, 1.5),  # pass a float for a fixed value
    #                    base_vel_command_type="random",  # "forward", "random", "forward+rotate", "human"
    #                    state_obs_names=state_observables_names,  # Desired quantities in the 'state' vec
    #                    )


    # Some robots require a change in the zero joint-space configuration. If provided apply it
    if cfg.qpos0_js is not None:
        env.mjModel.qpos0 = np.concatenate((env.mjModel.qpos0[:7], cfg.qpos0_js))

    env.reset(random=False)
    env.render()  # Pass in the first render call any mujoco.viewer.KeyCallbackType





    # Initialization of variables used in the main control loop --------------------------------
    # Set the reference for the state
    ref_pose = np.array([0, 0, cfg.hip_height])
    ref_base_lin_vel, ref_base_ang_vel = env.target_base_vel()
    ref_orientation = np.array([0.0, 0.0, 0.0])
    
    # TODO: I would suggest to create a DataClass for "BaseConfig" used in the PotatoModel controllers.
    ref_state = {}

    # Jacobian matrices
    jac_feet_prev = LegsAttr(*[np.zeros((3, env.mjModel.nv)) for _ in range(4)])
    jac_feet_dot = LegsAttr(*[np.zeros((3, env.mjModel.nv)) for _ in range(4)])
    # Torque vector
    tau = LegsAttr(*[np.zeros((env.mjModel.nv, 1)) for _ in range(4)])
    
    # State
    state_current, state_prev = {}, {}
    feet_pos = None
    feet_traj_geom_ids, feet_GRF_geom_ids = None, LegsAttr(FL=-1, FR=-1, RL=-1, RR=-1)
    legs_order = ["FL", "FR", "RL", "RR"]


    # Create HeightMap -----------------------------------------------------------------------
    if(cfg.simulation_params['visual_foothold_adaptation'] != 'blind'):
        resolution_vfa = 0.04
        dimension_vfa = 7
        heightmaps = LegsAttr(FL=HeightMap(n=dimension_vfa, dist_x=resolution_vfa, dist_y=resolution_vfa, mjModel=env.mjModel, mjData=env.mjData),
                        FR=HeightMap(n=dimension_vfa, dist_x=resolution_vfa, dist_y=resolution_vfa, mjModel=env.mjModel, mjData=env.mjData),
                        RL=HeightMap(n=dimension_vfa, dist_x=resolution_vfa, dist_y=resolution_vfa, mjModel=env.mjModel, mjData=env.mjData),
                        RR=HeightMap(n=dimension_vfa, dist_x=resolution_vfa, dist_y=resolution_vfa, mjModel=env.mjModel, mjData=env.mjData))
    else:
        heightmaps = None


    # Controller initialization -------------------------------------------------------------
    mpc_frequency = cfg.simulation_params['mpc_frequency']

    srbd_controller_interface = SRBDControllerInterface()

    if(cfg.mpc_params['type'] != 'sampling' and cfg.mpc_params['optimize_step_freq']):
        srbd_batched_controller_interface = SRBDBatchedControllerInterface()

    wb_interface = WBInterface(initial_feet_pos = env.feet_pos(frame='world'),
                               legs_order = legs_order)


    # --------------------------------------------------------------
    RENDER_FREQ = 30  # Hz
    N_EPISODES = 500
    N_STEPS_PER_EPISODE = 2000 if env.base_vel_command_type != "human" else 20000
    last_render_time = time.time()

    state_obs_history, ctrl_state_history = [], []
    for episode_num in tqdm(range(N_EPISODES), desc="Episodes"):

        ep_state_obs_history, ep_ctrl_state_history = [], []
        for _ in range(N_STEPS_PER_EPISODE):
            step_start = time.time()


            # Update value from SE or Simulator ----------------------
            feet_pos = env.feet_pos(frame='world')
            hip_pos = env.hip_positions(frame='world')
            base_lin_vel = env.base_lin_vel(frame='world')
            base_ang_vel = env.base_ang_vel(frame='world')
            base_ori_euler_xyz = env.base_ori_euler_xyz
            base_pos = env.base_pos
            
            # Get the reference base velocity in the world frame
            ref_base_lin_vel, ref_base_ang_vel = env.target_base_vel()
            
            # Get the inertia matrix
            if(cfg.simulation_params['use_inertia_recomputation']):
                inertia = env.get_base_inertia().flatten()  # Reflected inertia of base at qpos, in world frame
            else:
                inertia = cfg.inertia.flatten()

            # Get the qpos and qvel
            qpos, qvel = env.mjData.qpos, env.mjData.qvel
            
            # Get Centrifugal, Coriolis, Gravity for the swing controller
            legs_mass_matrix = env.legs_mass_matrix
            legs_qfrc_bias = env.legs_qfrc_bias

            # Compute feet jacobian
            feet_jac = env.feet_jacobians(frame='world', return_rot_jac=False)
            
            # Compute jacobian derivatives of the contact points
            jac_feet_dot = (feet_jac - jac_feet_prev) / simulation_dt  # Finite difference approximation
            jac_feet_prev = feet_jac  # Update previous Jacobians
            
            # Compute feet velocities
            feet_vel = LegsAttr(**{leg_name: feet_jac[leg_name] @ env.mjData.qvel for leg_name in legs_order})

            # Idx of the leg
            legs_qvel_idx = env.legs_qvel_idx




            # Update the state and reference -------------------------
            state_current, \
            ref_state, \
            contact_sequence, \
            ref_feet_pos, \
            contact_sequence_dts, \
            contact_sequence_lenghts, \
            step_height, \
            optimize_swing = wb_interface.update_state_and_reference(base_pos,
                                                    base_lin_vel,
                                                    base_ori_euler_xyz,
                                                    base_ang_vel,
                                                    feet_pos,
                                                    hip_pos,
                                                    heightmaps,
                                                    legs_order,
                                                    simulation_dt,
                                                    ref_base_lin_vel,
                                                    ref_base_ang_vel)



    
            # Solve OCP ---------------------------------------------------------------------------------------
            if env.step_num % round(1 / (mpc_frequency * simulation_dt)) == 0:

                nmpc_GRFs,  \
                nmpc_footholds, \
                optimize_swing, \
                best_sample_freq = srbd_controller_interface.compute_control(state_current,
                                                                        ref_state,
                                                                        contact_sequence,
                                                                        inertia,
                                                                        wb_interface.pgg,
                                                                        ref_feet_pos,
                                                                        contact_sequence_dts,
                                                                        contact_sequence_lenghts,
                                                                        step_height,
                                                                        optimize_swing)
                

                # Update the gait
                if(cfg.mpc_params['type'] != 'sampling' and cfg.mpc_params['optimize_step_freq']):
                    best_sample_freq = srbd_batched_controller_interface.optimize_gait(state_current,
                                                                            ref_state,
                                                                            contact_sequence,
                                                                            inertia,
                                                                            wb_interface.pgg,
                                                                            ref_feet_pos,
                                                                            contact_sequence_dts,
                                                                            contact_sequence_lenghts,
                                                                            step_height,
                                                                            optimize_swing)


            
            
            # Compute Swing and Stance Torque ---------------------------------------------------------------------------
            tau = wb_interface.compute_stance_and_swing_torque(simulation_dt,
                                                        qvel,
                                                        feet_jac,
                                                        jac_feet_dot,
                                                        feet_pos,
                                                        feet_vel,
                                                        legs_qfrc_bias,
                                                        legs_mass_matrix,
                                                        nmpc_GRFs,
                                                        nmpc_footholds,
                                                        legs_qvel_idx,
                                                        tau,
                                                        optimize_swing,
                                                        best_sample_freq)
            

            
            # Set control and mujoco step ----------------------------------------------------------------------
            action = np.zeros(env.mjModel.nu)
            action[env.legs_tau_idx.FL] = tau.FL
            action[env.legs_tau_idx.FR] = tau.FR
            action[env.legs_tau_idx.RL] = tau.RL
            action[env.legs_tau_idx.RR] = tau.RR

            action_noise = np.random.normal(0, 2, size=env.mjModel.nu)
            action += action_noise

            state, reward, is_terminated, is_truncated, info = env.step(action=action)


            # Store the history of observations and control -------------------------------------------------------
            ep_state_obs_history.append(state)
            base_lin_vel_err = ref_base_lin_vel - base_lin_vel
            base_ang_vel_err = ref_base_ang_vel - base_ang_vel
            base_poz_z_err = ref_state["ref_position"][2] - base_pos[2]
            ctrl_state = np.concatenate((base_lin_vel_err, base_ang_vel_err, [base_poz_z_err], wb_interface.pgg._phase_signal))
            ep_ctrl_state_history.append(ctrl_state)


            # Render only at a certain frequency -----------------------------------------------------------------
            if time.time() - last_render_time > 1.0 / RENDER_FREQ or env.step_num == 1:
                _, _, feet_GRF = env.feet_contact_state(ground_reaction_forces=True)

                # Plot the swing trajectory
                feet_traj_geom_ids = plot_swing_mujoco(viewer=env.viewer,
                                                       swing_traj_controller=wb_interface.stc,
                                                       swing_period=wb_interface.stc.swing_period,
                                                       swing_time=LegsAttr(FL=wb_interface.stc.swing_time[0],
                                                                           FR=wb_interface.stc.swing_time[1],
                                                                           RL=wb_interface.stc.swing_time[2],
                                                                           RR=wb_interface.stc.swing_time[3]),
                                                       lift_off_positions=wb_interface.frg.lift_off_positions,
                                                       nmpc_footholds=nmpc_footholds,
                                                       ref_feet_pos=ref_feet_pos,
                                                       geom_ids=feet_traj_geom_ids)
                
                
                # Update and Plot the heightmap
                if(cfg.simulation_params['visual_foothold_adaptation'] != 'blind'):
                    #if(stc.check_apex_condition(current_contact, interval=0.01)):
                    for leg_id, leg_name in enumerate(legs_order):
                        data = heightmaps[leg_name].data#.update_height_map(ref_feet_pos[leg_name], yaw=env.base_ori_euler_xyz[2])
                        if(data is not None):
                            for i in range(data.shape[0]):
                                for j in range(data.shape[1]):
                                        heightmaps[leg_name].geom_ids[i, j] = render_sphere(viewer=env.viewer,
                                                                                            position=([data[i][j][0][0],data[i][j][0][1],data[i][j][0][2]]),
                                                                                            diameter=0.01,
                                                                                            color=[0, 1, 0, .5],
                                                                                            geom_id=heightmaps[leg_name].geom_ids[i, j]
                                                                                            )
                            
                # Plot the GRF
                for leg_id, leg_name in enumerate(legs_order):
                    feet_GRF_geom_ids[leg_name] = render_vector(env.viewer,
                                                                vector=feet_GRF[leg_name],
                                                                pos=feet_pos[leg_name],
                                                                scale=np.linalg.norm(feet_GRF[leg_name]) * 0.005,
                                                                color=np.array([0, 1, 0, .5]),
                                                                geom_id=feet_GRF_geom_ids[leg_name])

                env.render()
                last_render_time = time.time()


            # Reset the environment if the episode is terminated ------------------------------------------------
            if env.step_num > N_STEPS_PER_EPISODE or is_terminated or is_truncated:
                if is_terminated:
                    print("Environment terminated")
                else:
                    state_obs_history.append(ep_state_obs_history)
                    ctrl_state_history.append(ep_ctrl_state_history)
                env.reset(random=False)
                
                wb_interface.reset(initial_feet_pos = env.feet_pos(frame='world'))
                

    env.close()


