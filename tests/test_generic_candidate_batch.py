from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from campaign_workflow.batch_campaign import (
    BatchCampaignError,
    build_batch_campaign_plan,
)


class GenericCandidateBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.template = self.root / "template"
        self.template.mkdir()
        (self.template / "campaign.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "campaign_name": "template",
                    "case_manifest": "cases.tsv",
                    "case_id_column": "CASE_ID",
                    "case_name_column": "CASE_NAME",
                }
            ),
            encoding="utf-8",
        )
        (self.template / "input_template.py").write_text(
            "print('template')\n", encoding="utf-8"
        )
        self.candidates = self.root / "candidate_batch.tsv"
        self.plan = self.root / "batch_campaign_plan.json"
        self.output = self.root / "iteration"
        self._write_plan()
        self._write_candidates()

    def tearDown(self) -> None:
        self.tmpdir.cleanup()

    def _write_plan(self) -> None:
        self.plan.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "plan_type": "optimizer_candidate_batch",
                    "problem_kind": "multichannel",
                    "campaign_template": {
                        "campaign_json": "campaign.json",
                        "input_template": "input_template.py",
                    },
                    "candidate_batch_contract": {
                        "schema_version": 1,
                        "required_columns": [
                            "CASE_ID",
                            "CASE_NAME",
                            "PLASMA_ION_DENSITY_M3",
                            "WRITE_FIELD_DIAGNOSTIC",
                        ],
                        "numeric_columns": [
                            "PLASMA_ION_DENSITY_M3",
                            "WRITE_FIELD_DIAGNOSTIC",
                        ],
                        "integer_columns": [
                            "CASE_ID",
                            "WRITE_FIELD_DIAGNOSTIC",
                        ],
                        "choices": {"WRITE_FIELD_DIAGNOSTIC": ["0", "1"]},
                        "bounds": {
                            "PLASMA_ION_DENSITY_M3": [5.0e24, 5.0e25]
                        },
                        "summary_columns": ["OPT_SAMPLE_SOURCE"],
                    },
                }
            ),
            encoding="utf-8",
        )

    def _write_candidates(self, density: str = "1.0e25") -> None:
        with self.candidates.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "CASE_ID",
                    "CASE_NAME",
                    "PLASMA_ION_DENSITY_M3",
                    "WRITE_FIELD_DIAGNOSTIC",
                    "OPT_SAMPLE_SOURCE",
                ],
                delimiter="\t",
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerow(
                {
                    "CASE_ID": 0,
                    "CASE_NAME": "mc_i000_c000_reference",
                    "PLASMA_ION_DENSITY_M3": density,
                    "WRITE_FIELD_DIAGNOSTIC": 1,
                    "OPT_SAMPLE_SOURCE": "reference",
                }
            )

    def test_generic_contract_accepts_multichannel_columns(self) -> None:
        plan = build_batch_campaign_plan(
            candidate_batch=self.candidates,
            batch_plan=self.plan,
            template_campaign_root=self.template,
            output_campaign_root=self.output,
            campaign_name="multichannel_iter_000",
        )
        self.assertEqual(len(plan.rows), 1)
        self.assertEqual(plan.rows[0]["OPT_SAMPLE_SOURCE"], "reference")
        self.assertIsNotNone(plan.candidate_batch_contract)

    def test_generic_contract_rejects_out_of_range_numeric_value(self) -> None:
        self._write_candidates(density="9.0e25")
        with self.assertRaisesRegex(BatchCampaignError, "outside"):
            build_batch_campaign_plan(
                candidate_batch=self.candidates,
                batch_plan=self.plan,
                template_campaign_root=self.template,
                output_campaign_root=self.output,
                campaign_name="multichannel_iter_000",
            )


if __name__ == "__main__":
    unittest.main()
