#!/usr/bin/env python3
"""Validate every scheduled beam frame and both animations before raw cleanup."""
import argparse
import csv
import json
import math
from pathlib import Path
from PIL import Image

SPECIES = {'preionized_background_electrons', 'nitrogen_ionized_electrons'}
SCOPES = SPECIES | {'all_electrons'}


def rows(path):
    with path.open(newline='') as stream:
        return list(csv.DictReader(stream))


def validate(case):
    resolved = json.loads((case/'resolved_parameters.json').read_text())
    expected = resolved['beam_diagnostic_iterations']
    if len(expected) < 2 or expected != sorted(set(expected)):
        raise ValueError('Invalid diagnostic schedule')
    frames = rows(case/'beam_analysis/beam_evolution.csv')
    keys = [(int(r['iteration']), r['species_scope']) for r in frames]
    if len(keys) != len(set(keys)) or set(keys) != {(it,s) for it in expected for s in SCOPES}:
        raise ValueError('Beam CSV does not cover the scheduled species/frame product')
    for r in frames:
        if r['population_status'] not in {'ok','empty'} or r.get('read_error'):
            raise ValueError('Unavailable/invalid beam frame')
        if float(r['energy_threshold_MeV']) != 50 or r['forward_only'].lower() != 'true':
            raise ValueError('Incorrect hard50/forward selection')
        if not math.isfinite(float(r['charge_pC'])) or float(r['charge_pC']) < 0:
            raise ValueError('Invalid charge')
    summaries = rows(case/'beam_analysis/beam_evolution_summary.csv')
    if len(summaries) != 3 or {r['species_scope'] for r in summaries} != SCOPES:
        raise ValueError('Missing species summaries')
    for r in summaries:
        if r['exit_snapshot_status'] in {'missing','unavailable','invalid_particle_data'}:
            raise ValueError('Missing/invalid exact exit snapshot')
    movies = case/'beam_animations'
    manifest = rows(movies/'frame_manifest.csv')
    if [int(r['iteration']) for r in manifest] != expected:
        raise ValueError('Animation frame schedule mismatch')
    meta = json.loads((movies/'visualization_metadata.json').read_text())
    if set(meta['species']) != SPECIES or set(meta['rho_fields']) != {'rho_'+s for s in SPECIES}:
        raise ValueError('Animation species provenance mismatch')
    for name in ['phase_space_full.gif','rho_electrons_full.gif']:
        with Image.open(movies/name) as movie:
            if movie.n_frames != len(expected):
                raise ValueError('GIF lost scheduled frames')
            for i in range(movie.n_frames):
                movie.seek(i); movie.load()
    # Charge in the combined animation must agree with the sum of species metrics.
    for row in manifest:
        charge = sum(float(r['charge_pC']) for r in frames if int(r['iteration']) == int(row['iteration']) and r['species_scope'] in SPECIES)
        if not math.isclose(charge, float(row['charge_forward_50MeV_pC']), rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError('Combined animation and beam reduction disagree')
    for it in expected:
        selected = [r for r in frames if int(r['iteration']) == it]
        total = next(float(r['charge_pC']) for r in selected if r['species_scope']=='all_electrons')
        if not math.isclose(total, sum(float(r['charge_pC']) for r in selected if r['species_scope'] in SPECIES), rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError('Combined scope charge mismatch')
    return dict(status='pass', contract_id='clpu_n2_beam_products_v1',
                n_frames=len(expected), species=sorted(SPECIES),
                integration_status={r['species_scope']:r['integration_status'] for r in summaries},
                coverage_fraction={r['species_scope']:float(r['coverage_fraction']) for r in summaries})


def main():
    p=argparse.ArgumentParser(); p.add_argument('--case-dir',type=Path,required=True)
    args=p.parse_args(); result=validate(args.case_dir)
    (args.case_dir/'beam_analysis/validation.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))

if __name__=='__main__':
    main()
