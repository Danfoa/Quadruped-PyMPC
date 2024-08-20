import numpy as np

from quadruped_pympc.helpers.periodic_gait_generator import PeriodicGaitGenerator

from gym_quadruped.utils.quadruped_utils import LegsAttr

from quadruped_pympc import config as cfg

class SRBDBatchedControllerInterface:
    def __init__(self, ):
        
        self.type = cfg.mpc_params['type']
        self.mpc_dt = cfg.mpc_params['dt']
        self.horizon = cfg.mpc_params['horizon']
        self.optimize_step_freq = cfg.mpc_params['optimize_step_freq']
        self.num_parallel_computations = cfg.mpc_params['num_parallel_computations']
        self.sampling_method = cfg.mpc_params['sampling_method']
        self.control_parametrization = cfg.mpc_params['control_parametrization']
        self.num_sampling_iterations = cfg.mpc_params['num_sampling_iterations']
        self.sigma_cem_mppi = cfg.mpc_params['sigma_cem_mppi']
        self.step_freq_available = cfg.mpc_params['step_freq_available']


        from quadruped_pympc.controllers.gradient.nominal.centroidal_nmpc_gait_adaptive import \
                    Acados_NMPC_GaitAdaptive
        
        self.batched_controller = Acados_NMPC_GaitAdaptive()
        


    

    def optimize_gait(self, 
                        state_current,
                        ref_state,
                        contact_sequence,
                        inertia,
                        pgg,
                        ref_feet_pos,
                        contact_sequence_dts,
                        contact_sequence_lenghts,
                        step_height,
                        optimize_swing):
    

        

        best_sample_freq = pgg.step_freq
        if self.optimize_step_freq and optimize_swing == 1:
            contact_sequence_temp = np.zeros((len(self.step_freq_available), 4, self.horizon))
            for j in range(len(self.step_freq_available)):
                pgg_temp = PeriodicGaitGenerator(duty_factor=pgg.duty_factor,
                                                    step_freq=self.step_freq_available[j],
                                                    gait_type=pgg.gait_type,
                                                    horizon=self.horizon)
                pgg_temp.set_phase_signal(pgg.phase_signal)
                contact_sequence_temp[j] = pgg_temp.compute_contact_sequence(contact_sequence_dts=contact_sequence_dts, 
                                                                                contact_sequence_lenghts=contact_sequence_lenghts)


            costs, \
            best_sample_freq = self.batched_controller.compute_batch_control(state_current,
                                                                        ref_state,
                                                                        contact_sequence_temp)

            
            

        
        return best_sample_freq
        