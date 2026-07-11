import json, re, unittest
from pathlib import Path
from tools.plugin_packet_validator import validate_packet
ROOT=Path(__file__).parents[1]
class Task6AArtifacts(unittest.TestCase):
    def test_plugin_packet_validator_rejects_nested_bad_objects_and_exact_schema(self):
        source=json.loads((ROOT/'evals/plugin-submission-readiness/cases.json').read_text(encoding='utf-8'))
        validate_packet(source)
        bad_objects=[
            ("nested type", lambda d: d['positive'][0].update({'type':'other'})),
            ("nested shape", lambda d: d['negative'][0].update({'result_shape':'verbose'})),
            ("schema enum", lambda d: d['result_schema'].__setitem__('result','PASS|FAIL')),
            ("nested path", lambda d: d['positive'][0].update({'metadata':{'deep':['C:/Users/name/repo']}})),
            ("nested credential", lambda d: d['positive'][0].update({'metadata':{'deep':{'api_key':'sk-test-secret-value'}}})),
            ("nested hidden schema key", lambda d: d['positive'][0].update({'metadata':{'chain_of_thought':'omit'}})),
        ]
        for label, mutate in bad_objects:
            with self.subTest(label=label):
                candidate=json.loads(json.dumps(source))
                mutate(candidate)
                with self.assertRaises(ValueError):
                    validate_packet(candidate)

    def test_external_review_kit_is_vendor_neutral_and_observable(self):
        base=ROOT/'evals/external-review'
        for name in ('README.md','prompt-audit.md','prompt-functional.md','result-template.md','safety-redaction.md'):
            self.assertTrue((base/name).is_file())
        text='\n'.join(p.read_text(encoding='utf-8') for p in base.glob('*.md')).lower()
        self.assertIn('hidden reasoning',text); self.assertIn('model/version',text); self.assertIn('visible',text)
        self.assertNotRegex(text,r'[a-z]:[\\/](?:users|home)[\\/]')
        self.assertNotRegex(text,r'(?:sk|rk|ghp|xox[baprs]-)[a-z0-9_-]{8,}', re.I)
        self.assertNotIn('chain_of_thought', text); self.assertIn('forbid', text)
    def test_plugin_packet_has_exact_cases_schema_and_honest_gaps(self):
        data=json.loads((ROOT/'evals/plugin-submission-readiness/cases.json').read_text(encoding='utf-8'))
        validate_packet(data)
        self.assertEqual(len(data['positive']),5); self.assertEqual(len(data['negative']),3); self.assertEqual(data['status'],'NOT READY')
        cases=data['positive']+data['negative']; self.assertEqual(len({c['id'] for c in cases}),8)
        for kind, group in (('positive',data['positive']),('negative',data['negative'])):
            for case in group:
                self.assertEqual(case['kind'],kind); self.assertIn('type',case)
                self.assertIn('starter_prompt',case); self.assertIn('expected_behavior',case); self.assertIn('result_shape',case)
                self.assertNotRegex(json.dumps(case).lower(),r'hidden reasoning|api key')
                if kind=='negative': self.assertRegex(case['expected_behavior'].lower(),r'refuse|clarif|safe fallback')
                for value in case.values(): self.assertNotRegex(str(value),r'[a-z]:[\\/](?:users|home)[\\/]',re.I)
        schema=data['result_schema']
        for field in ('case_id','result','visible_output','evidence','file_line','diff','tool_events','disposition','status','limitations'):
            self.assertIn(field,schema)
        for enum in ('PASS','FAIL','INCONCLUSIVE'): self.assertIn(enum,schema['result'])
        readme=(ROOT/'evals/plugin-submission-readiness/README.md').read_text(encoding='utf-8').lower()
        for gap in ('publisher identity','production logo','category','website','support','privacy','terms','availability','release notes'):
            self.assertIn(gap,readme)
        self.assertIn('not ready',readme)
