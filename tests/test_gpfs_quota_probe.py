import unittest
from campaign_workflow.guards import _parse_mmlsquota_output, GuardError

HEADER='mmlsquota:user:HEADER:version:reserved:reserved:filesystemName:quotaType:id:name:blockUsage:blockQuota:blockLimit:blockInDoubt:blockGrace:filesUsage:filesQuota:filesLimit:filesInDoubt:filesGrace:remarks:fid:filesetname:'
ROW='mmlsquota:user:0:1:::home:USR:1000:juan:100:1000:2000:50:none:1:0:0:0:none::::'

class GpfsQuotaTests(unittest.TestCase):
    def test_header_driven_kb_and_in_doubt_conservative_soft_limit(self):
        result=_parse_mmlsquota_output(HEADER+'\n'+ROW,'home','juan')
        self.assertEqual(result['quota_used_bytes'],150*1024)
        self.assertEqual(result['quota_limit_bytes'],1000*1024)

    def test_other_user_or_filesystem_is_not_accepted(self):
        for fs,user in [('other','juan'),('home','other')]:
            with self.assertRaises(GuardError):
                _parse_mmlsquota_output(HEADER+'\n'+ROW,fs,user)

    def test_duplicate_or_malformed_rows_are_not_accepted(self):
        for output in [HEADER+'\n'+ROW+'\n'+ROW,HEADER+'\n'+ROW.replace(':100:',':NaN:')]:
            with self.assertRaises(GuardError):
                _parse_mmlsquota_output(output,'home','juan')
