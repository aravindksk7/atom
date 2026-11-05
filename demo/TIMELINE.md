# ATOM regression demo: presenter timeline

Generated from the recorded take. Video length: 27:01. Each time is when the step banner appears in the video, and how long it stays until the next one. "Say" is about the length shown beside it at a steady pace, and `timeline.srt` holds the same words as cues for a video player or editor.

## At a glance

| Time | Part |
|---|---|
| 00:10 | **1. Configure**: Select the business date and the ETL sets |
| 03:21 | **2. Execute**: 25 jobs: ETLs, file watcher, CrushFTP, AWS S3 and step validation |
| 06:10 | **3. Reconcile**: 10 comparisons across files, databases and BO reports |
| 08:32 | **4. Restart**: Continue from the failure, without repeating what already passed |
| 12:22 | **5. Validate**: Expected changes + no unintended regression + BO reports |
| 20:51 | **6. Report**: Consolidated execution, reconciliation and pass/fail evidence |
| 21:33 | **7. CI/CD**: Run the regression from a GitLab pipeline |

## Script

### 00:10 Part 1: Configure

Select the business date and the ETL sets

**00:14-00:26 (12s) Step 1: Open the saved sequences**

On screen: A sequence is the versioned definition of what runs, and in what order

Say (12s): Opening Sequences. A sequence is ATOM's saved, versioned definition of what runs and in what order. It mirrors the production schedule, so we test the order we actually run.

**00:26-00:40 (14s) Step 2: Select Nightly Regression**

On screen: 35 jobs: 25 to execute and 10 reconciliations against Production

Say (14s): Selecting Nightly Regression. It holds 35 jobs: 25 that execute, and 10 that compare against Production. They are ETLs, file watchers, CrushFTP fetches, AWS S3 publishing and validation steps, all in one flow.

**00:40-00:54 (14s) Step 3: Series, parallel and hybrid in one flow**

On screen: Each stage runs its jobs one after another, or together

Say (11s): Opening the flow editor to see how the jobs are arranged. This is where we choose series, parallel or a mix of the two, stage by stage.

**00:54-03:03 (130s) Step 4: Preview the execution order**

On screen: One job on a line runs alone, several on a line run together

Say (19s): The execution order preview. Each line is a level: one job on a line runs alone, and several on a line run together. Set 1 is in series, Set 2 is in parallel, and Set 3 is a hybrid: one job, then three together, then one again.

Spare 111s: a candidate to trim in your editor.

**03:03-03:15 (12s) Step 5: Enter the business date**

On screen: run_date=2026-06-03 goes to every job in the run

Say (12s): Starting a run, and entering the business date. This single value goes to every ETL, every query, every file path and every BO report prompt in the run.

**03:15-03:21 (6s) Step 6: Launch**

On screen: From here on ATOM drives everything

Say (5s): Launching. From here on ATOM drives everything, with no manual steps.

### 03:21 Part 2: Execute

25 jobs: ETLs, file watcher, CrushFTP, AWS S3 and step validation

**03:25-03:39 (14s) Step 7: Watch the run live**

On screen: Started, elapsed and jobs done, updated every second

Say (14s): Opening the Execution Monitor to watch the run live. Up top is the run's start time, its running clock and how many of the 35 jobs are done. All of it updates every second.

**03:39-04:02 (23s) Step 8: A progress bar for every job**

On screen: Percent comes from each job's recent runs. Stripes alone mean no estimate

Say (23s): Every job gets its own progress bar. The percent is estimated from that job's recent runs, and stops at 95 until the job really finishes. This file watcher is waiting on a signal, so history can't predict it: moving stripes mean running, with no estimate. The jobs after it are queued, because their turn hasn't come.

**04:02-04:55 (53s) Step 9: Set 1: SERIES**

On screen: File watcher → CrushFTP fetch → 3 ETLs → validation, one after another

Say (23s): Set 1 runs in series. Here the file watcher is waiting for the upstream arrival signal on CrushFTP, and nothing starts until it lands. There it is. Now we fetch the Production file, then three ETLs run one after another, each passed the business date. A validation step then confirms the output is there before anything continues.

Spare 30s: a candidate to trim in your editor.

**04:55-05:34 (40s) Step 10: Set 2: PARALLEL**

On screen: 6 ETLs running at the same time, then one validation step

Say (12s): Set 2 runs in parallel. Six ETLs start together, and you can see several running at the same moment. The validation step waits until all six have finished.

**05:34-05:57 (22s) Step 11: Set 3: HYBRID**

On screen: One ETL → 3 together → one ETL → validation

Say (13s): Set 3 is a hybrid. One ETL runs alone, then three run together, then one more runs alone, followed by validation. Real batch schedules look like this, and ATOM reproduces it.

**05:57-06:10 (13s) Step 12: Set 4: SERIES with AWS S3**

On screen: 4 ETLs → publish to AWS S3 → validate the upload

Say (8s): Set 4 finishes the execution in series, then publishes the generated files to AWS S3 and validates that they arrived.

### 06:10 Part 3: Reconcile

10 comparisons across files, databases and BO reports

**06:13-06:38 (25s) Step 13: 10 comparisons**

On screen: 7 file and database comparisons in parallel, then 3 BO reports in series

Say (14s): The reconciliation runs. Seven comparisons run in parallel: generated CSV and XML files against baselines in AWS S3, Postgres against Oracle, and databases against the CrushFTP flat file. Three BusinessObjects comparisons then run in series.

**06:38-07:10 (32s) Step 14: End-to-end Gantt**

On screen: One time axis: a staircase is series, stacked bars are parallel

Say (32s): The run has finished, and the Monitor has switched from progress bars to a Gantt of the whole run. Here is the total elapsed time. Each row is one job on one time axis, so the shape shows the orchestration: a staircase where jobs run in series, and stacked bars where they run in parallel. Here are Set 2's six ETLs, all running together. And here is the report that never ran: it has a row, but no bar.

**07:10-07:33 (23s) Step 15: Color by job type**

On screen: SAP, file and reconciliation jobs at a glance

Say (12s): The same chart, colored by job type instead of status: SAP jobs, file jobs and reconciliations. One look shows where the night's time goes. Back to status for the rest.

**07:33-07:58 (25s) Step 16: Open the result**

On screen: The run has stopped: let us see why

Say (5s): The run has stopped, so let's see the result in History.

**07:58-08:12 (14s) Step 17: Files vs Production baselines**

On screen: Each file is paired with its baseline, matched by key, and compared

Say (14s): The CSV reconciliation. ATOM paired each generated file with its Production baseline in AWS S3, matched the records by key and compared every field. These counts are the total across all the file pairs.

**08:12-08:23 (11s) Step 18: Postgres vs Oracle**

On screen: Balances match, trades have differences to review

Say (8s): Database comparisons: QA Postgres against Production Oracle. The balances match, and the trades have differences we will review shortly.

**08:23-08:32 (9s) Step 19: Databases vs the CrushFTP flat file**

On screen: The file fetched in Set 1, compared with Postgres and Oracle

Say (4s): Databases against the flat file fetched from CrushFTP.

### 08:32 Part 4: Restart

Continue from the failure, without repeating what already passed

**08:35-08:53 (18s) Step 20: An unintended failure**

On screen: The Production Daily Sales file was not delivered, so this job failed

Say (18s): Here is an unintended failure. The Production copy of the Daily Sales report was not delivered, so this job failed with an error. It isn't a data problem, it's an operational one. The next BO report never ran, because it depends on this one.

**08:53-09:06 (13s) Step 21: Restart from failure**

On screen: The file has been delivered. Passed jobs are kept, and only the rest run

Say (13s): Operations have now delivered the missing file. In a traditional setup we would run the whole batch again. ATOM can restart from the failure instead, so none of the ETLs repeat.

**09:06-09:25 (19s) Step 22: Continuing from the failure**

On screen: 25 execution jobs carried over, not run again

Say (16s): Watching the restarted run. These grey bars are carried over: all 25 execution jobs already passed, so they are not run again. ATOM continues from the failed job, and the report that was blocked now runs as well.

**09:25-09:40 (15s) Step 23: Gantt of the restart**

On screen: Bars only for what ran. Carried-over jobs took no time

Say (15s): The Gantt of the restarted run. Only the jobs that needed to run have bars, and the carried-over jobs have rows with no bar. This is the time we saved by not running the whole sequence again.

**09:40-10:02 (21s) Step 24: Recent runs of the sequence**

On screen: Start, end and elapsed time for every run

Say (19s): Every run of this sequence, with its start, end and elapsed time. At the top is the restart we just ran, and below it the full run it continued from, with earlier nights under those. Comparing nights like this shows when a batch starts to slow down.

**10:02-12:22 (140s) Step 25: The restarted run**

On screen: 35 jobs: 30 passed and 5 failed, with the ETLs carried over

Say (18s): The restarted run, linked to the original. Of 35 jobs, 30 passed and 5 failed, and those 5 are now true data findings rather than operational problems. This ETL says "carried over": it ran once, in the first run, and its result is kept.

Spare 122s: a candidate to trim in your editor.

### 12:22 Part 5: Validate

Expected changes + no unintended regression + BO reports

**12:25-12:36 (11s) Step 26: Review the differences**

On screen: Fields we meant to change should change, and nothing else should

Say (10s): Opening the Differences Explorer. This is the heart of the regression story: fields we meant to change should change, and everything else should not.

**12:36-13:54 (78s) Step 27: Progression: the expected change**

On screen: fee_amount was meant to change, so a reviewer accepts it with a reason

Say (15s): Progression validation. Searching the CSV differences for fee_amount, the field this change was designed to alter. These are expected, and a reviewer accepts them with a reason. This is a human decision, recorded with a note.

Spare 63s: a candidate to trim in your editor.

**13:54-14:07 (12s) Step 28: Regression: what should not have changed**

On screen: A missing record and an additional record: both are exceptions

Say (10s): Regression validation. Everything still open is unexpected. Here are two exceptions the change does not explain: a record missing from Test, and an additional record.

**14:07-16:55 (168s) Step 29: XML outputs**

On screen: After the expected change, one incorrect value remains

Say (10s): The XML outputs get the same review. After accepting fee_amount, one exception remains: an incorrect notional value in a field that should not have changed.

Spare 158s: a candidate to trim in your editor.

**16:55-20:11 (196s) Step 30: Postgres vs Oracle**

On screen: After the expected change, one wrong notional remains

Say (8s): QA Postgres against Production Oracle. Same pattern: the fee change is accepted, and one wrong notional stays open.

Spare 188s: a candidate to trim in your editor.

**20:11-20:22 (11s) Step 31: Postgres vs the flat file**

On screen: One position is missing from Postgres

Say (11s): QA Postgres against the CrushFTP flat file. Nothing here is expected, so nothing is accepted: one position is in the Production file but missing from Postgres.

**20:22-20:38 (16s) Step 32: BO reports**

On screen: QA vs Production: the Sales Summary amount total differs

Say (16s): BusinessObjects reports. ATOM pulled each impacted report live from QA for this business date and compared it with the Production report. Two of the three reports match. The Sales Summary does not: its amount total differs from Production.

**20:38-20:51 (13s) Step 33: The tally**

On screen: 6 exceptions stay open across the whole run

Say (11s): The tally for the whole run. Hundreds of differences were reviewed. The expected ones are accepted, and exactly six exceptions stay open for someone to resolve.

### 20:51 Part 6: Report

Consolidated execution, reconciliation and pass/fail evidence

**20:55-21:33 (38s) Step 34: Consolidated evidence**

On screen: Execution, reconciliation and BO results in one report

Say (12s): Opening Reports for the consolidated evidence: execution, reconciliation and BO results, all from the same run. This is what we would hand to a release manager or an auditor.

### 21:33 Part 7: CI/CD

Run the regression from a GitLab pipeline

**21:38-24:10 (152s) Step 35: Generate the GitLab job**

On screen: ATOM writes the pipeline job for this sequence

Say (10s): Everything so far can run from CI/CD. ATOM generates the GitLab job for this sequence, ready to paste into the project's pipeline file.

Spare 142s: a candidate to trim in your editor.

**24:10-24:28 (18s) Step 36: Run the pipeline (simulated GitLab runner)**

On screen: It runs ATOM's real CI script with a scoped token

Say (18s): This is a simulated GitLab runner, as the page says, running ATOM's real CI script with a scoped CI token. In a real project this starts on a commit or a schedule. Here we press Run pipeline, and it launches the same 35-job regression.

**24:28-26:23 (114s) Step 37: The pipeline follows the result**

On screen: ATOM found exceptions, so the pipeline fails and blocks the release

Say (19s): ATOM reports back to the pipeline, and the pipeline's result follows ATOM's result. Because this run finds real exceptions, the pipeline fails, so a regression can't reach release unnoticed. Accepted differences apply to the run they were reviewed in, so a fresh run reports them again.

Spare 95s: a candidate to trim in your editor.

**26:23-26:36 (14s) Step 38: CI Runs in ATOM**

On screen: Every pipeline-triggered run, with its commit, branch and pipeline link

Say (12s): Back in ATOM, the CI Runs tab lists every pipeline-triggered run with its commit, branch and a link to the pipeline, so there is one place to see them all.

**26:36-27:01 (24s) Step 39: Summary**

On screen: Progression + regression, with one consolidated result

Say (24s): Closing. One business date. Twenty-five jobs in series, parallel and hybrid flows, followed live and laid out on a Gantt, ten comparisons across files, databases and BusinessObjects reports, a restart that continued from the failure, and a pipeline that follows the result. We confirmed what should have changed, caught what should not have, and have the evidence in one place.
