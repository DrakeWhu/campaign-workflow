"""Cold Sobol launch evidence; the native per-iteration Gate B gates every array."""
from pathlib import Path
import csv
import json
from campaign_workflow.clpu_n2_submission import sha256_file, write_json_atomic

CONTRACT = 'clpu_n2_cold_sobol_launch_v1'


def validate_cold_plan(root):
    root = Path(root).resolve()
    workflow = json.loads((root/'optimization.json').read_text())
    optimizer = json.loads((root/'optimizer.json').read_text())
    if workflow.get('beam_campaign',{}).get('contract_id') != 'clpu_n2_beam_sobol_v1':
        raise ValueError('Not a beam cold-start campaign')
    if optimizer.get('source_campaigns') != [] or optimizer.get('gate_a_contract'):
        raise ValueError('Cold-start must not import warm observations/contracts')
    rec = optimizer['recommendation']; design = rec['initial_design']
    if rec['backend'] != 'sobol_then_morbo' or rec['n_candidates'] != 8:
        raise ValueError('Expected native Sobol backend with eight candidates')
    if any(design.get(k) != v for k,v in dict(sobol_points=32,initial_batch_size=8,continuation_batch_size=8,include_references=False).items()):
        raise ValueError('Expected 32 Sobol points in four batches of eight')
    if rec['min_observations'] <= 32:
        raise ValueError('BO must be disabled throughout this finite Sobol scan')
    policy = workflow['policy']
    if policy.get('cleanup_after_validation_required') is not True or policy.get('max_iterations') != 4:
        raise ValueError('Expected cleanup and four-iteration limit')
    if workflow['stopping'].get('max_iterations') != 4:
        raise ValueError('Stopping must end after four Sobol batches')
    campaign = root/'iterations/iter_000'
    with (campaign/'cases.tsv').open(newline='') as stream:
        batch=list(csv.DictReader(stream,delimiter='\t'))
    with (root/'optimizer_runs/iter_000/outputs/recommended_candidates.tsv').open(newline='') as stream:
        recommended=list(csv.DictReader(stream,delimiter='\t'))
    if len(batch)!=8 or [int(r['CASE_ID']) for r in batch]!=list(range(8)):
        raise ValueError('First batch is not eight materialized cases')
    if len(recommended)!=8 or {r['candidate_source'] for r in recommended}!={'sobol'} or [int(float(r['sobol_index'])) for r in recommended]!=list(range(8)):
        raise ValueError('Native first Sobol block is missing')
    config=json.loads((campaign/'campaign.json').read_text())
    if config['case_materialization']['env_constants'].get('CAP_BEAM_EVOLUTION')!='1':
        raise ValueError('Beam diagnostics not enabled')
    required={'beam_frames','beam_summary','beam_phase_movie','beam_rho_movie','beam_validation'}
    outputs={r['name'] for r in config['analysis']['outputs'] if r.get('required')}
    if not required <= outputs:
        raise ValueError('Beam products do not gate reduced validation')
    for r in batch:
        case=campaign/r['CASE_NAME']
        for name in ['input.py','case.env','state.json']:
            if not (case/name).is_file():
                raise ValueError(f'Missing materialized case file: {case/name}')
    if list(root.rglob('*.h5')) or list(root.rglob('post/sim_submitted.json')):
        raise ValueError('Cold launch root already contains simulations')
    return root


def build_cold_launch_receipt(root):
    root=validate_cold_plan(root)
    evidence={}
    for path in [root/'optimization.json',root/'optimizer.json',root/'iterations/iter_000/campaign.json',root/'iterations/iter_000/input_template.py',root/'iterations/iter_000/cases.tsv',root/'optimizer_runs/iter_000/outputs/recommended_candidates.tsv']:
        evidence[str(path.relative_to(root))]={'path':str(path),'sha256':sha256_file(path)}
    result=dict(schema_version=1,contract_id=CONTRACT,status='pass',allow_sbatch=True,
                optimization_root=str(root),iteration=0,runtime_validation_mode='per_iteration_gate_b',
                cleanup_execute=True,evidence=evidence)
    path=root/'provenance/clpu_n2_launch_gate.json'
    if path.exists():
        raise ValueError('Refusing to overwrite launch receipt')
    write_json_atomic(path,result)
    return path


def verify_cold_launch_receipt(root,payload,start_iteration):
    root=validate_cold_plan(root)
    config=json.loads((root/'optimization.json').read_text())
    if not config.get('guards',{}).get('quota',{}).get('quota_probe'):
        raise ValueError('A live user quota probe is required before launching the beam scan')
    if start_iteration!=0 or payload.get('iteration')!=0 or payload.get('status')!='pass' or payload.get('allow_sbatch') is not True or payload.get('cleanup_execute') is not True or payload.get('runtime_validation_mode')!='per_iteration_gate_b':
        raise ValueError('Invalid cold launch receipt')
    if payload.get('optimization_root')!=str(root):
        raise ValueError('Cold receipt belongs to another root')
    for record in payload.get('evidence',{}).values():
        if sha256_file(Path(record['path']))!=record['sha256']:
            raise ValueError('Cold launch evidence drift')
    if len(payload.get('evidence',{}))!=6:
        raise ValueError('Missing cold launch evidence')
