# ATOM regression demo: presenter timeline

Generated from the recorded take. Video length: 20:13. Each time is when the step banner appears in `atom-regression-demo.webm`.

## At a glance

| Time | Part |
|---|---|
| 00:09 | **1. Configure**: Select the business date and the ETL sets |
| 02:21 | **2. Execute**: 25 jobs: ETLs, file watcher, CrushFTP, AWS S3 and step validation |
| 03:42 | **3. Reconcile**: 10 comparisons across files, databases and BO reports |
| 06:11 | **4. Restart**: Continue from the failure, without repeating what already passed |
| 08:44 | **5. Validate**: Expected changes + no unintended regression + BO reports |
| 15:02 | **6. Report**: Consolidated execution, reconciliation and pass/fail evidence |
| 15:27 | **7. CI/CD**: Run the regression from a GitLab pipeline |

## Script

### 00:09 Part 1: Configure

Select the business date and the ETL sets

**00:12 Step 1: Open the saved sequences**

On screen: A sequence is the versioned definition of what runs, and in what order

Say: Opening Sequences. A sequence is ATOM's saved, versioned definition of what runs and in what order. It mirrors the production schedule, so we test the order we actually run.

**00:19 Step 2: Select Nightly Regression**

On screen: 35 jobs: 25 to execute and 10 reconciliations against Production

Say: Selecting Nightly Regression. It holds 35 jobs: 25 that execute, and 10 that compare against Production. They are ETLs, file watchers, CrushFTP fetches, AWS S3 publishing and validation steps, all in one flow.

**00:28 Step 3: Series, parallel and hybrid in one flow**

On screen: Each stage runs its jobs one after another, or together

Say: Opening the flow editor to see how the jobs are arranged. This is where we choose series, parallel or a mix of the two, stage by stage.

**00:44 Step 4: Preview the execution order**

On screen: One job on a line runs alone, several on a line run together

Say: The execution order preview. Each line is a level: one job on a line runs alone, and several on a line run together. Set 1 is in series, Set 2 is in parallel, and Set 3 is a hybrid: one job, then three together, then one again.

**01:37 Step 5: Enter the business date**

On screen: run_date=2026-06-03 goes to every job in the run

Say: Starting a run, and entering the business date. This single value goes to every ETL, every query, every file path and every BO report prompt in the run.

**02:14 Step 6: Launch**

On screen: From here on ATOM drives everything

Say: Launching. From here on ATOM drives everything, with no manual steps.

### 02:21 Part 2: Execute

25 jobs: ETLs, file watcher, CrushFTP, AWS S3 and step validation

**02:25 Step 7: Watch the run live**

On screen: Counters update as each job finishes

Say: Opening the Execution Monitor to watch the run live. The counters update as each job finishes, every job has a progress bar, and the Timeline view shows each job as a bar on a time axis.

**02:34 Step 8: Set 1: SERIES**

On screen: File watcher → CrushFTP fetch → 3 ETLs → validation, one after another

Say: Set 1 runs in series. Here the file watcher is waiting for the upstream arrival signal on CrushFTP, and nothing starts until it lands. There it is. Now we fetch the Production file, then three ETLs run one after another, each passed the business date. A validation step then confirms the output is there before anything continues.

**02:50 Step 9: Set 2: PARALLEL**

On screen: 6 ETLs running at the same time, then one validation step

Say: Set 2 runs in parallel. Six ETLs start together, and you can see several running at the same moment. The validation step waits until all six have finished.

**03:07 Step 10: Set 3: HYBRID**

On screen: One ETL → 3 together → one ETL → validation

Say: Set 3 is a hybrid. One ETL runs alone, then three run together, then one more runs alone, followed by validation. Real batch schedules look like this, and ATOM reproduces it.

**03:28 Step 11: Set 4: SERIES with AWS S3**

On screen: 4 ETLs → publish to AWS S3 → validate the upload

Say: Set 4 finishes the execution in series, then publishes the generated files to AWS S3 and validates that they arrived.

### 03:42 Part 3: Reconcile

10 comparisons across files, databases and BO reports

**03:48 Step 12: 10 comparisons**

On screen: 7 file and database comparisons in parallel, then 3 BO reports in series

Say: The reconciliation runs. Seven comparisons run in parallel: generated CSV and XML files against baselines in AWS S3, Postgres against Oracle, and databases against the CrushFTP flat file. Three BusinessObjects comparisons then run in series.

**04:57 Step 13: Open the result**

On screen: The run has stopped: let us see why

Say: The run has stopped, so let's see the result in History.

**05:42 Step 14: Files vs Production baselines**

On screen: Each file is paired with its baseline, matched by key, and compared

Say: The CSV reconciliation. ATOM paired each generated file with its Production baseline in AWS S3, matched the records by key and compared every field. These counts are the total across all the file pairs.

**05:52 Step 15: Postgres vs Oracle**

On screen: Balances match, trades have differences to review

Say: Database comparisons: QA Postgres against Production Oracle. The balances match, and the trades have differences we will review shortly.

**06:01 Step 16: Databases vs the CrushFTP flat file**

On screen: The file fetched in Set 1, compared with Postgres and Oracle

Say: Databases against the flat file fetched from CrushFTP.

### 06:11 Part 4: Restart

Continue from the failure, without repeating what already passed

**06:14 Step 17: An unintended failure**

On screen: The Production Daily Sales file was not delivered, so this job failed

Say: Here is an unintended failure. The Production copy of the Daily Sales report was not delivered, so this job failed with an error. It isn't a data problem, it's an operational one. The next BO report never ran, because it depends on this one.

**06:27 Step 18: Restart from failure**

On screen: The file has been delivered. Passed jobs are kept, and only the rest run

Say: Operations have now delivered the missing file. In a traditional setup we would run the whole batch again. ATOM can restart from the failure instead, so none of the ETLs repeat.

**06:35 Step 19: Continuing from the failure**

On screen: 25 execution jobs carried over, not run again

Say: Watching the restarted run. All 25 execution jobs are carried over as already passed, and ATOM continues from the failed job. The report that was blocked now runs as well.

**06:41 Step 20: The restarted run**

On screen: 35 jobs: 30 passed and 5 failed, with the ETLs carried over

Say: The restarted run, linked to the original. Of 35 jobs, 30 passed and 5 failed, and those 5 are now true data findings rather than operational problems. This ETL says "carried over": it ran once, in the first run, and its result is kept.

### 08:44 Part 5: Validate

Expected changes + no unintended regression + BO reports

**08:48 Step 21: Review the differences**

On screen: Fields we meant to change should change, and nothing else should

Say: Opening the Differences Explorer. This is the heart of the regression story: fields we meant to change should change, and everything else should not.

**08:59 Step 22: Progression: the expected change**

On screen: fee_amount was meant to change, so a reviewer accepts it with a reason

Say: Progression validation. Searching the CSV differences for fee_amount, the field this change was designed to alter. These are expected, and a reviewer accepts them with a reason. This is a human decision, recorded with a note.

**10:50 Step 23: Regression: what should not have changed**

On screen: A missing record and an additional record: both are exceptions

Say: Regression validation. Everything still open is unexpected. Here are two exceptions the change does not explain: a record missing from Test, and an additional record.

**11:00 Step 24: XML outputs**

On screen: After the expected change, one incorrect value remains

Say: The XML outputs get the same review. After accepting fee_amount, one exception remains: an incorrect notional value in a field that should not have changed.

**12:16 Step 25: Postgres vs Oracle**

On screen: After the expected change, one wrong notional remains

Say: QA Postgres against Production Oracle. Same pattern: the fee change is accepted, and one wrong notional stays open.

**14:37 Step 26: Postgres vs the flat file**

On screen: One position is missing from Postgres

Say: QA Postgres against the CrushFTP flat file. Nothing here is expected, so nothing is accepted: one position is in the Production file but missing from Postgres.

**14:47 Step 27: BO reports**

On screen: QA vs Production: the Sales Summary amount total differs

Say: BusinessObjects reports. ATOM pulled each impacted report live from QA for this business date and compared it with the Production report. Two of the three reports match. The Sales Summary does not: its amount total differs from Production.

**14:56 Step 28: The tally**

On screen: 6 exceptions stay open across the whole run

Say: The tally for the whole run. Hundreds of differences were reviewed. The expected ones are accepted, and exactly six exceptions stay open for someone to resolve.

### 15:02 Part 6: Report

Consolidated execution, reconciliation and pass/fail evidence

**15:06 Step 29: Consolidated evidence**

On screen: Execution, reconciliation and BO results in one report

Say: Opening Reports for the consolidated evidence: execution, reconciliation and BO results, all from the same run. This is what we would hand to a release manager or an auditor.

### 15:27 Part 7: CI/CD

Run the regression from a GitLab pipeline

**15:31 Step 30: Generate the GitLab job**

On screen: ATOM writes the pipeline job for this sequence

Say: Everything so far can run from CI/CD. ATOM generates the GitLab job for this sequence, ready to paste into the project's pipeline file.

**17:27 Step 31: Run the pipeline (simulated GitLab runner)**

On screen: It runs ATOM's real CI script with a scoped token

Say: This is a simulated GitLab runner, as the page says, running ATOM's real CI script with a scoped CI token. In a real project this starts on a commit or a schedule. Here we press Run pipeline, and it launches the same 35-job regression.

**17:33 Step 32: The pipeline follows the result**

On screen: ATOM found exceptions, so the pipeline fails and blocks the release

Say: ATOM reports back to the pipeline, and the pipeline's result follows ATOM's result. Because this run finds real exceptions, the pipeline fails, so a regression can't reach release unnoticed. Accepted differences apply to the run they were reviewed in, so a fresh run reports them again.

**18:47 Step 33: CI Runs in ATOM**

On screen: Every pipeline-triggered run, with its commit, branch and pipeline link

Say: Back in ATOM, the CI Runs tab lists every pipeline-triggered run with its commit, branch and a link to the pipeline, so there is one place to see them all.

**19:03 Step 34: Summary**

On screen: Progression + regression, with one consolidated result

Say: Closing. One business date. Twenty-five jobs in series, parallel and hybrid flows, ten comparisons across files, databases and BusinessObjects reports, a restart that continued from the failure, and a pipeline that follows the result. We confirmed what should have changed, caught what should not have, and have the evidence in one place.
