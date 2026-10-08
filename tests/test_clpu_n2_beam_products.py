import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from PIL import Image

SCRIPT=Path(__file__).resolve().parents[1]/'examples/sunrise/corrected_capillary/validate_beam_products.py'
spec=importlib.util.spec_from_file_location('beam_validator',SCRIPT)
validator=importlib.util.module_from_spec(spec);spec.loader.exec_module(validator)


def table(path,rows):
    with path.open('w',newline='') as stream:
        w=csv.DictWriter(stream,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)


class ProductsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.case=Path(self.temp.name);self.analysis=self.case/'beam_analysis';self.analysis.mkdir()
        self.movies=self.case/'beam_animations';self.movies.mkdir()
        (self.case/'resolved_parameters.json').write_text(json.dumps({'beam_diagnostic_iterations':[0,10]}))
        self.frames=[dict(iteration=it,species_scope=s,charge_pC=2 if s=='all_electrons' else 1,
                          population_status='ok',energy_threshold_MeV=50,forward_only=True,read_error='')
                     for it in [0,10] for s in validator.SCOPES]
        table(self.analysis/'beam_evolution.csv',self.frames)
        table(self.analysis/'beam_evolution_summary.csv',
              [dict(species_scope=s,integration_status='partial',coverage_fraction=.9,exit_snapshot_status='ok') for s in validator.SCOPES])
        table(self.movies/'frame_manifest.csv',[dict(iteration=it,charge_forward_50MeV_pC=2) for it in [0,10]])
        (self.movies/'visualization_metadata.json').write_text(json.dumps({'species':list(validator.SPECIES),'rho_fields':['rho_'+s for s in validator.SPECIES]}))
        for name in ['phase_space_full.gif','rho_electrons_full.gif']:
            images=[Image.new('RGB',(10,10),color) for color in ['red','blue']]
            images[0].save(self.movies/name,save_all=True,append_images=images[1:])

    def test_valid_products_with_explicit_partial_coverage(self):
        self.assertEqual(validator.validate(self.case)['status'],'pass')

    def test_missing_species_frame_blocks_cleanup_validation(self):
        table(self.analysis/'beam_evolution.csv',self.frames[:-1])
        with self.assertRaisesRegex(ValueError,'scheduled species'):
            validator.validate(self.case)

    def test_invalid_frame_blocks_cleanup_validation(self):
        self.frames[0]['population_status']='unavailable'
        table(self.analysis/'beam_evolution.csv',self.frames)
        with self.assertRaisesRegex(ValueError,'Unavailable'):
            validator.validate(self.case)

    def test_movie_charge_must_match_reduction(self):
        table(self.movies/'frame_manifest.csv',[dict(iteration=it,charge_forward_50MeV_pC=3) for it in [0,10]])
        with self.assertRaisesRegex(ValueError,'disagree'):
            validator.validate(self.case)

    def test_truncated_movie_blocks_cleanup_validation(self):
        Image.new('RGB',(10,10),'red').save(self.movies/'rho_electrons_full.gif')
        with self.assertRaisesRegex(ValueError,'lost scheduled'):
            validator.validate(self.case)
