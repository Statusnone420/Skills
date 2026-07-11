import json, re, unittest
from pathlib import Path
ROOT=Path(__file__).parents[1]
class Task6AArtifacts(unittest.TestCase):
    def test_external_review_kit_is_vendor_neutral_and_observable(self):
        base=ROOT/'evals/external-review'
        for name in ('README.md','prompt-audit.md','prompt-functional.md','result-template.md','safety-redaction.md'):
            self.assertTrue((base/name).is_file())
        text='\n'.join(p.read_text(encoding='utf-8') for p in base.glob('*.md')).lower()
        self.assertIn('hidden reasoning',text); self.assertIn('model/version',text); self.assertIn('visible',text)
        self.assertNotRegex(text,r'[a-z]:[\\/](?:users|home)[\\/]')
    def test_plugin_packet_has_exact_cases_schema_and_honest_gaps(self):
        data=json.loads((ROOT/'evals/plugin-submission-readiness/cases.json').read_text(encoding='utf-8'))
        self.assertEqual(len(data['positive']),5); self.assertEqual(len(data['negative']),3); self.assertEqual(data['status'],'NOT READY')
        for case in data['positive']+data['negative']:
            self.assertIn('starter_prompt',case); self.assertIn('expect',case); self.assertNotRegex(case['starter_prompt'].lower(),r'hidden reasoning|api key')
        readme=(ROOT/'evals/plugin-submission-readiness/README.md').read_text(encoding='utf-8').lower()
        for gap in ('publisher identity','production logo','category','website','support','privacy','terms','availability','release notes'):
            self.assertIn(gap,readme)
        self.assertIn('not ready',readme)
