from __future__ import annotations

import copy
import unittest

from campaign_workflow.core.state import initial_state_document
from campaign_workflow.core.transitions import (
    simulation_failure_transition,
    simulation_running_transition,
    simulation_submit_transition,
    mark_sim_done_transition,
)
from campaign_workflow.core.tsv_cases import CaseRecord


class SimulationLifecycleTransitionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.case = CaseRecord(
            case_id=0,
            case_name="000_fake_case",
            row={"CASE_ID": "0", "CASE_NAME": "000_fake_case"},
        )
        self.created = initial_state_document(self.case)

    def test_created_case_can_be_marked_submitted(self) -> None:
        submitted = simulation_submit_transition(
            self.created,
            reason="submitted to external simulation backend",
        )

        self.assertEqual(submitted["state"], "Submitted")
        self.assertEqual(submitted["history"][-1]["from"], "Created")
        self.assertEqual(submitted["history"][-1]["to"], "Submitted")
        self.assertEqual(submitted["history"][-1]["operation"], "mark_sim_submitted")

    def test_submitted_case_can_be_marked_running(self) -> None:
        submitted = simulation_submit_transition(
            self.created,
            reason="submitted to external simulation backend",
        )

        running = simulation_running_transition(
            submitted,
            reason="external simulation command started",
        )

        self.assertEqual(running["state"], "Running")
        self.assertEqual(running["history"][-1]["from"], "Submitted")
        self.assertEqual(running["history"][-1]["to"], "Running")
        self.assertEqual(running["history"][-1]["operation"], "mark_sim_running")

    def test_running_case_can_be_marked_sim_done(self) -> None:
        submitted = simulation_submit_transition(
            self.created,
            reason="submitted to external simulation backend",
        )
        running = simulation_running_transition(
            submitted,
            reason="external simulation command started",
        )

        done = mark_sim_done_transition(
            running,
            reason="external simulation command completed successfully",
        )

        self.assertEqual(done["state"], "Sim_done")
        self.assertEqual(done["history"][-1]["from"], "Running")
        self.assertEqual(done["history"][-1]["to"], "Sim_done")
        self.assertEqual(done["history"][-1]["operation"], "mark_sim_done")

    def test_running_case_can_be_marked_failed(self) -> None:
        submitted = simulation_submit_transition(
            self.created,
            reason="submitted to external simulation backend",
        )
        running = simulation_running_transition(
            submitted,
            reason="external simulation command started",
        )

        failed = simulation_failure_transition(
            running,
            reason="external simulation command returned non-zero exit code",
        )

        self.assertEqual(failed["state"], "Failed")
        self.assertEqual(failed["history"][-1]["from"], "Running")
        self.assertEqual(failed["history"][-1]["to"], "Failed")
        self.assertEqual(failed["history"][-1]["operation"], "mark_sim_failed")

    def test_created_to_sim_done_backfill_still_exists(self) -> None:
        done = mark_sim_done_transition(
            self.created,
            reason="completion evidence found for existing campaign case",
        )

        self.assertEqual(done["state"], "Sim_done")
        self.assertEqual(done["history"][-1]["from"], "Created")
        self.assertEqual(done["history"][-1]["to"], "Sim_done")

    def test_created_case_cannot_be_marked_running_without_submission(self) -> None:
        with self.assertRaisesRegex(ValueError, "simulation running mark is not allowed"):
            simulation_running_transition(
                self.created,
                reason="external simulation command started",
            )

    def test_submitted_case_cannot_be_marked_sim_done_without_running_state(self) -> None:
        submitted = simulation_submit_transition(
            self.created,
            reason="submitted to external simulation backend",
        )

        with self.assertRaisesRegex(ValueError, "mark_sim_done is not allowed"):
            mark_sim_done_transition(
                submitted,
                reason="external simulation command completed successfully",
            )

    def test_input_state_document_is_not_mutated(self) -> None:
        before = copy.deepcopy(self.created)

        _ = simulation_submit_transition(
            self.created,
            reason="submitted to external simulation backend",
        )

        self.assertEqual(self.created, before)


if __name__ == "__main__":
    unittest.main()