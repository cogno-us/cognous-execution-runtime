"""Fixture mutation tests; no claim of live interception or authorization."""
import copy
import json
from pathlib import Path
import unittest
from tools.openappa.fixture_contract import validate_fixture


class FixtureContractTests(unittest.TestCase):
    def setUp(self):
        self.bundle = json.loads((Path(__file__).parent / 'fixtures/openappa/protocol1.json').read_text())

    def test_native_operation_and_subordinate_attempt(self):
        operation, attempt = validate_fixture(self.bundle)
        self.assertEqual(operation.operation_id, 'synthetic-operation')
        self.assertEqual(attempt.discriminator, operation)
        self.assertNotEqual(attempt.attempt_id, operation.operation_id)

    def test_every_host_identity_substitution_is_rejected(self):
        for key in self.bundle['host_context']:
            with self.subTest(key=key):
                changed = copy.deepcopy(self.bundle)
                changed['host_context'][key] = 'substituted'
                with self.assertRaises(ValueError): validate_fixture(changed)

    def test_changed_request_bytes_are_rejected(self):
        self.bundle['request_json'] = self.bundle['request_json'].replace('50', '51')
        with self.assertRaises(ValueError): validate_fixture(self.bundle)

    def test_missing_host_context_is_rejected(self):
        del self.bundle['host_context']['tenant_id']
        with self.assertRaises(ValueError): validate_fixture(self.bundle)

    def test_source_substitution_is_rejected(self):
        self.bundle['upstream_sha'] = 'unreviewed'
        with self.assertRaises(ValueError): validate_fixture(self.bundle)

    def test_fixture_cannot_claim_authority(self):
        self.bundle['authority_effect'] = 'allow'
        with self.assertRaises(ValueError): validate_fixture(self.bundle)


if __name__ == '__main__': unittest.main()
