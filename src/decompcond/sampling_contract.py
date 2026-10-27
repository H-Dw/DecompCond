"""Describe the frozen sampler to the planner without importing model libraries."""

from .io import digest


def planning_sampling_contract(config):
    sampling = config.sampling
    step_size = sampling.integration_end_time / sampling.steps
    extra_prediction = sampling.readout == "extra_prediction"
    last_prediction_time = (sampling.steps - 1) * step_size
    readout_time = (
        sampling.integration_end_time - sampling.final_epsilon
        if extra_prediction
        else last_prediction_time
    )
    description = dict(
        generator="FLOWR.ROOT",
        weights_frozen=True,
        agent_controls="active condition IDs and their update-index windows only",
        steps=sampling.steps,
        segment_multiple=sampling.segment_multiple,
        indexing="zero-based, half-open [start,end); end never names an extra update",
        integration_start_time=0.0,
        integration_end_time=sampling.integration_end_time,
        uniform_step_size=step_size,
        known_atom_network_time=sampling.known_time,
        inactive_atom_network_time="current global time",
        integrator_time="global time only; never substitute known atom time",
        last_loop_prediction=dict(
            update_index=sampling.steps - 1, global_time=last_prediction_time
        ),
        readout=dict(
            mode=sampling.readout,
            global_time=readout_time,
            extra_forward_count=int(extra_prediction),
            forward_count_per_candidate=(
                sampling.steps * config.backend.model_args["sc_recycle_steps"]
                + int(extra_prediction)
            ),
            output="unprojected clean model prediction, not the integrated state",
            active_set_source="last update's active condition set",
            final_post_update_projection_can_affect_output=extra_prediction,
            final_reference_overwrite=False,
        ),
        self_condition=dict(
            initial_coords=sampling.initial_self_condition_coords,
            reference_overwrite=False,
            on_condition_release="retain current state and prediction cache; stop projection",
        ),
        condition_channels=["element", "reference_xyz", "selected_existing_bonds"],
        projection_points=["before prediction", "after native update"],
        planning_windows=[
            dict(
                start=start,
                end=start + sampling.segment_multiple,
                first_prediction_global_time=start * step_size,
                last_prediction_global_time=(start + sampling.segment_multiple - 1)
                * step_size,
            )
            for start in range(0, sampling.steps, sampling.segment_multiple)
        ],
        comparison_policy=(
            "Baseline, every proposed path and independent confirmation use this same sampler. "
            "Use only the current teacher's discovery feedback from this run; do not import "
            "old sampler results as current baseline or confirmation as planning evidence."
        ),
    )
    return dict(sha256=digest(description), **description)
