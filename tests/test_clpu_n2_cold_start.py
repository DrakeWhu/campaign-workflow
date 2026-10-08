import csv
import json
from pathlib import Path
import tempfile
import unittest
from campaign_workflow.clpu_n2_cold_start import build_cold_launch_receipt
from campaign_workflow.clpu_n2_submission import require_launch_gate, ClpuN2SubmissionError

class ColdStartTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.write('optimization.json',{'beam_campaign':{'contract_id':'clpu_n2_beam_sobol_v1'},'policy':{'cleanup_after_validation_required':True,'max_iterations':4},'stopping':{'max_iterations':4},'guards':{'quota':{'quota_probe':{'kind':'lfs_quota_user'}}}})
        self.write('optimizer.json',{'source_campaigns':[],'recommendation':{'backend':'sobol_then_morbo','n_candidates':8,'min_observations':1000000,'initial_design':{'sobol_points':32,'initial_batch_size':8,'continuation_batch_size':8,'include_references':False}}})
        self.write('iterations/iter_000/campaign.json',{'case_materialization':{'env_constants':{'CAP_BEAM_EVOLUTION':'1'}},'analysis':{'outputs':[{'name':n,'required':True} for n in ['beam_frames','beam_summary','beam_phase_movie','beam_rho_movie','beam_validation']]}})
        (self.root/'iterations/iter_000/input_template.py').write_text('# reviewed fixture\n')
        self.tsv('iterations/iter_000/cases.tsv',[{'CASE_ID':i,'CASE_NAME':f'{i:03d}_case'} for i in range(8)])
        self.tsv('optimizer_runs/iter_000/outputs/recommended_candidates.tsv',[{'candidate_source':'sobol','sobol_index':i} for i in range(8)])
        for i in range(8):
            case=self.root/f'iterations/iter_000/{i:03d}_case';case.mkdir()
            for name in ['input.py','case.env','state.json']:
                (case/name).write_text('fixture')

    def write(self,name,value):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))

    def tsv(self,name,rows):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('w',newline='') as stream:
            w=csv.DictWriter(stream,fieldnames=list(rows[0]),delimiter='\t');w.writeheader();w.writerows(rows)

    def test_cold_receipt_requires_per_iteration_runtime_gate(self):
        build_cold_launch_receipt(self.root)
        _,receipt,_=require_launch_gate(optimization_root=self.root,start_iteration=0)
        self.assertEqual(receipt['runtime_validation_mode'],'per_iteration_gate_b')
        self.assertNotIn('gate_b_status',receipt)  # no invented PICMI success

    def test_configuration_drift_is_rejected(self):
        build_cold_launch_receipt(self.root)
        path=self.root/'optimizer.json';path.write_text(path.read_text()+'\n')
        with self.assertRaisesRegex(ClpuN2SubmissionError,'evidence drift'):
            require_launch_gate(optimization_root=self.root,start_iteration=0)

    def test_launch_without_live_quota_probe_is_rejected(self):
        path=self.root/'optimization.json';data=json.loads(path.read_text());data.pop('guards')
        path.write_text(json.dumps(data));build_cold_launch_receipt(self.root)
        with self.assertRaisesRegex(ClpuN2SubmissionError,'live user quota'):
            require_launch_gate(optimization_root=self.root,start_iteration=0)

    def test_warm_sources_are_rejected(self):
        data=json.loads((self.root/'optimizer.json').read_text());data['source_campaigns']=[{'campaign_root':'old'}]
        self.write('optimizer.json',data)
        with self.assertRaisesRegex(ValueError,'warm observations'):
            build_cold_launch_receipt(self.root)

    def test_no_bo_during_sobol_scan(self):
        data=json.loads((self.root/'optimizer.json').read_text());data['recommendation']['min_observations']=32
        self.write('optimizer.json',data)
        with self.assertRaisesRegex(ValueError,'BO must be disabled'):
            build_cold_launch_receipt(self.root)
