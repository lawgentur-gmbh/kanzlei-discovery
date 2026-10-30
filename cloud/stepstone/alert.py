"""Last-resort cloud alert, including failures before a DB connection exists."""
import sys
from runtime import notify
if '--test'in sys.argv:notify({'test':True,'database_changed':False},test=True)
else:notify({'issues':['cloud_workflow_failed_or_timed_out'],'action':'Check the weekly workflow run. Database history and existing jobs were not deleted.'})
