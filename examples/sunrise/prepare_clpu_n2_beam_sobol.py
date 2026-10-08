#!/usr/bin/env python3
"""Configure the native optimizer/workflow for a finite diagnostic Sobol scan.

No simulation or sbatch is performed by this preparation command.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


def config(root, optimizer):
    data=json.loads((optimizer/'examples/optimizer_clpu_baseline_soft50_single_objective_sunrise.json').read_text())
    data['parameter_space']['version']=3
    data['parameter_space']['ranges']['nitrogen_fraction']=[0.,.01]
    data['parameter_space'].pop('references',None)
    data['optimization_history']['optimization_name']=root.name
    data['optimization_history']['particle_observation_contract']='clpu_n2_plateau_all_electrons_v1'
    rec=data['recommendation']; rec['min_observations']=1000000
    rec['initial_design'].update(sobol_points=32,initial_batch_size=8,continuation_batch_size=8,include_references=False,seed=20261008)
    rec['seed']=20261008
    rec['botorch']['enabled']=False
    data['candidate_batch']['campaign_template']={'campaign_json':'campaign.json','input_template':'input_template.py'}
    return data


def prepare(root, wf, optimizer, guiding, *, require_clean=True, quota_probe=None):
    root=root.resolve(); wf=wf.resolve(); optimizer=optimizer.resolve(); guiding=guiding.resolve()
    if root.exists():
        raise ValueError(f'Refusing existing campaign root: {root}')
    if require_clean:
        for repo in (wf,optimizer,guiding):
            if subprocess.check_output(['git','-C',str(repo),'status','--porcelain'],text=True).strip():
                raise ValueError(f'Dirty repository: {repo}')
    for repo,ancestor in [(guiding,'da0ab3528eb41cbe80d05c91e4962f89e33c9117'),(optimizer,'73dab76305f547581c57b70a900706374c929141')]:
        subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',ancestor,'HEAD'],check=True)
    head=subprocess.check_output(['git','-C',str(guiding),'rev-parse','HEAD'],text=True).strip()
    root.mkdir(parents=True); (root/'iterations').mkdir(); (root/'loop_logs').mkdir()
    envdir=root/'env'; envdir.mkdir()
    example=wf/'examples/sunrise/corrected_capillary'
    template=root/'template_campaign'; template.mkdir()
    shutil.copyfile(example/'campaign_nitrogen_beam_evolution.json',template/'campaign.json')
    shutil.copyfile(example/'nitrogen_input_template.py',template/'input_template.py')
    payload=config(root,optimizer)
    (root/'optimizer.json').write_text(json.dumps(payload,indent=2)+'\n')
    envfile=envdir/'campaign-beam-sobol.sh'
    exports={'CAMPAIGN_OPTIMIZER_SRC':optimizer,'CAMPAIGN_WORKFLOW_SRC':wf,'WFLOW_SRC':wf,
             'GUIDING_ANALYSIS_SRC':guiding,'GUIDING_ANALYSIS_ROOT':guiding,
             'CLPU_N2_OPTIMIZER_ROOT':optimizer,'GUIDING_ANALYSIS_REQUIRED_COMMIT':head,
             'CW_STOP_ON_CASE_FAILURE':'1','PYTHONPATH':f'{optimizer}:{wf}:{guiding}'}
    text='#!/usr/bin/env bash\nset -Eeuo pipefail\nsource "${HOME}/apps/env/campaign-optimizer.sh"\nmodule load Git/2.41.0\n'
    text+='\n'.join('export '+k+'='+shlex.quote(str(v)) for k,v in exports.items())+'\n'
    envfile.write_text(text); envfile.chmod(0o755)
    workflow=json.loads((example/'optimization.json').read_text())
    workflow['optimization_name']=root.name
    workflow['beam_campaign']={'contract_id':'clpu_n2_beam_sobol_v1','guiding_analysis_commit':head,'objective_policy':'diagnostic_sobol_only_no_bo'}
    workflow['optimizer'].update(working_directory=str(optimizer),env_script=str(envfile),optimizer_config=str(root/'optimizer.json'))
    workflow['campaign_preparation']['campaign_name_template']=root.name+'_iter_{next_iteration:03d}'
    limits=dict(max_iterations=4,max_total_materialized_cases=32,max_total_submitted_cases=32,max_cases_per_submit=8,max_unsubmitted_materialized_cases=8)
    workflow['policy'].update(limits,cleanup_after_validation_required=True,min_reduced_valid_to_continue=8,min_valid_fraction_to_continue=1.,max_failed_fraction_to_continue=0.)
    workflow['guards']['campaign_size'].update(limits)
    if quota_probe is not None:
        workflow['guards']['quota']['quota_probe']=quota_probe
    workflow['stopping']['max_iterations']=4
    (root/'optimization.json').write_text(json.dumps(workflow,indent=2)+'\n')
    runtime=dict(os.environ,PYTHONPATH=f'{optimizer}:{wf}:{guiding}')
    def run(*args):
        subprocess.run([sys.executable,*map(str,args)],env=runtime,check=True,cwd=str(root))
    run('-m','campaign_optimizer.cli.run_iteration','--config',root/'optimizer.json','--iteration',0,'--build-candidate-batch','--build-report')
    out=root/'optimizer_runs/iter_000/outputs'
    run('-m','campaign_workflow.cli.prepare_batch_campaign','--candidate-batch',out/'candidate_batch.tsv','--batch-plan',out/'batch_campaign_plan.json','--template-campaign-root',template,'--output-campaign-root',root/'iterations/iter_000','--campaign-name',root.name+'_iter_000','--execute')
    run('-m','campaign_workflow.cli.materialize_cases','--campaign-root',root/'iterations/iter_000')
    run('-m','campaign_workflow.cli.init_case_states','--campaign-root',root/'iterations/iter_000')
    run('-m','campaign_workflow.cli.optimizer_tick','--optimization-root',root,'--init-state')
    from campaign_workflow.clpu_n2_cold_start import build_cold_launch_receipt
    build_cold_launch_receipt(root)
    print(f'PREPARED_ROOT={root}\nWORKFLOW_ENV={envfile}\nNO_SBATCH=1\nNO_WARPX_EVOLUTION=1')
    return root


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--workflow-root',type=Path,required=True)
    p.add_argument('--optimizer-root',type=Path,required=True)
    p.add_argument('--guiding-analysis-root',type=Path,required=True)
    p.add_argument('--quota-probe-json',type=Path,help='Native workflow live user quota probe configuration')
    a=p.parse_args(); probe=json.loads(a.quota_probe_json.read_text()) if a.quota_probe_json else None
    prepare(a.root,a.workflow_root,a.optimizer_root,a.guiding_analysis_root,quota_probe=probe)

if __name__=='__main__':
    main()
