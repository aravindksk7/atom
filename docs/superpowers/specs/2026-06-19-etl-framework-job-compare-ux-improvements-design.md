# ETL Framework Job Launcher & Compare Tab UX Improvements

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Goal
Improve the user experience of the Job Launcher/Launch tab and Compare tab in the ETL Test Framework web GUI, focusing on high-impact usability fixes that reduce configuration burden, improve discoverability, and streamline common workflows.

## Architecture
Incremental enhancements to existing Alpine.js frontend (`frontend/app.js`, `frontend/index.html`, `frontend/styles.css`) with no backend changes required. All improvements are client-side UX enhancements that work with the existing API.

## Tech Stack
- Python 3.14 (backend remains unchanged)
- FastAPI (backend remains unchanged)
- Alpine.js 3.x (frontend)
- Tailwind CSS (via CDN)
- No additional dependencies required

---

## 1. Job Launcher/Launch Tab Improvements

### 1.1 Job Catalog Enhancements
- [ ] Add search/filter box above job catalog to filter by name, tags, or description
- [ ] Show job status indicators (small colored dots) showing last run status (passed/failed/running)
- [ ] Enable multi-select with Shift-click for faster job selection
- [ ] Show job type and tags as badges (already partially implemented, improve visibility)
- [ ] Add "Select All" / "Select None" buttons

### 1.2 Settings Card Improvements
- [ ] Visually group related settings using background shades or borders:
  - Connection Settings (Source Env, Target Env, Saved Config)
  - Execution Settings (Execution Mode, Max Workers, Max Duration)
  - Comparison Settings (Backend, Chunk Size, Tolerance)
  - Advanced Options (Health Check, Metrics, NULL handling, Hash precheck, Retry policy)
- [ ] Add tooltips (using `title` attribute or Alpine.js tooltips) for complex settings:
  - `chunk_size`: "Number of rows to process at once. 0 disables chunking."
  - `hash_precheck`: "Use hash comparison before full value comparison to speed up processing"
  - `null_equals_null`: "Treat two NULL values as equal during comparison"
- [ ] Enhance saved config dropdown to show both config name and environment (e.g., "dev-sql (Dev)")
- [ ] Remember last-used settings per user session (using localStorage)

### 1.3 Job Modal Improvements
- [ ] Reorganize tabs in logical flow:
  1. Basic Info (Name, Description, Job Type, Enabled)
  2. Job-specific Settings (SQL Query, BO Report details, etc. - varies by job type)
  3. Dependencies (Depends On job names)
  4. DQ Rules (Data Quality Rules configuration)
  5. Tags (Tags, etc.)
- [ ] Add inline validation with real-time feedback:
  - SQL Query: Show "Query looks valid" as user types (basic syntax check)
  - Key Columns: Warn if column name doesn't exist in query (basic validation)
  - Dependencies: Warn if dependency job doesn't exist
- [ ] Improve DQ Rules section:
  - Add "Rule Templates" dropdown with common patterns:
    * "Price must be positive" (column_mean_between with min=0)
    * "Email format validation" (match_regex with standard email pattern)
    * "ID must be not null" (not_null rule)
    * "Status code range" (column_mean_between for HTTP status codes)
  - Show rule description as user selects rule type from dropdown
  - Add ability to reorder rules via drag-and-drop or up/down buttons
- [ ] Add "Save as Template" button in Job Modal header
- [ ] Template management:
  - Clicking "Save as Template" prompts for template name
  - Templates stored in localStorage (or could be backend-saved in future)
  - New Job modal shows "Use Template" dropdown listing saved templates
  - Selecting a template pre-fills all relevant fields

### 1.4 Execution Sequence Improvements
- [ ] Allow dragging jobs to reorder (in addition to existing up/down buttons)
- [ ] Show estimated total duration based on historical run times (if available in localStorage)
- [ ] Add "Clear All" button to remove all jobs from sequence
- [ ] Add "Invert Selection" button to toggle all job checkboxes
- [ ] Show warning if sequence contains circular dependencies (basic validation)

---

## 2. Compare Tab Improvements

### 2.1 Template System
- [ ] Add template dropdown in both BO Report and Reconciliation sub-tab headers
- [ ] Pre-defined templates:
  - **BO Report Tab**:
    * "Daily BO Report Compare" (Live API sources, standard key columns)
    * "Weekly Report Trend Analysis" (Compare to baseline)
    * "Ad-hoc File Upload Comparison" (Upload vs Upload)
  - **Reconciliation Tab**:
    * "Daily Reconciliation vs Baseline" (Stored run vs last baseline)
    * "Production File Validation" (File upload vs stored run)
    * "Environment-to-Environment Diff" (Two live environments)
- [ ] Templates pre-configure source types, key columns, exclude columns, and other relevant settings
- [ ] Add "Save as Template" button to save current configuration as reusable template
- [ ] Template persistence via localStorage (or backend in future)

### 2.2 BO Report Tab Specific Improvements
- [ ] Add "Save as Baseline" checkbox that automatically pins the comparison result as baseline when complete
- [ ] Improve source pickers with better default suggestions:
  - Remember last-used source types per side (A/B)
  - Suggest document/report IDs based on history
- [ ] Add "Swap Sides" button to easily exchange Source A and Source B configurations

### 2.3 Reconciliation Tab Specific Improvements
- [ ] Add "Quick Compare" mode that:
  - Automatically selects the last successful run as Source A
  - Requires only Source B configuration (file upload or stored run selection)
  - Reduces setup time for common regression testing scenarios
- [ ] In Stored Run Diff mode:
  - Sort run dropdowns by date (most recent first)
  - Show run status and timestamp in dropdown options
- [ ] Add ability to compare more than two runs (stretch goal)

### 2.4 Results Panel Improvements
- [ ] Add "Export Comparison Settings" button to save current comparison configuration as template
- [ ] Add visualization toggle to show mismatch distribution charts directly in results panel:
  - Bar chart showing top-N mismatched values
  - Option to chart by column, source value, or target value
  - Integrated with existing `/api/runs/<run_id>/results/<result_id>/mismatch-distribution` endpoint
- [ ] Improve mismatch acceptance workflow:
  - Show acceptance note directly in mismatch row when accepted (no need to expand)
  - Add "Accept All Visible" button for bulk acceptance when appropriate
  - Add filter to show only accepted/rejected/unaccepted mismatches

### 2.5 Cross-tab Consistency Improvements
- [ ] Standardize iconography: Use same icons for similar actions across tabs
- [ ] Standardize button styles: Primary/Secondary/Danger consistent everywhere
- [ ] Add consistent help system:
  - "?" icons next to complex fields with tooltips explaining purpose
  - Tooltips triggered on hover/focus
  - Consistent placement and styling
- [ ] Add keyboard shortcuts:
  - Ctrl+S / Cmd+S: Save current job or comparison configuration
  - Enter: Run tests (in Job Launcher) or Run comparison (in Compare tab)
  - Escape: Close modals
  - Tab navigation: Logical focus order in forms

---

## 3. Implementation Considerations

### 3.1 Backwards Compatibility
- [ ] All changes are purely additive - no existing functionality is removed or changed
- [ ] Templates and settings stored in localStorage do not affect server or other users
- [ ] Existing workflows continue to work exactly as before
- [ ] No API or database schema changes required

### 3.2 Performance Impact
- [ ] All enhancements are client-side only - no impact on API response times
- [ ] localStorage read/write operations are minimal and asynchronous
- [ ] No additional dependencies or library loading required
- [ ] Initial page load time unaffected (same CSS/JS files)

### 3.3 Testing Approach
- [ ] Manual verification of all new UI interactions
- [ ] Verify existing functionality still works after changes
- [ ] Test template save/load functionality
- [ ] Test keyboard shortcuts
- [ ] Verify responsive design still works on mobile/narrow screens
- [ ] Ensure accessibility (screen reader compatibility) is maintained or improved

### 3.4 File Changes
- [ ] Modify `frontend/app.js` - Add new Alpine.js state and methods for:
  * Template management (save/load/delete)
  * Job catalog search/filter
  * Settings grouping and tooltips
  * Job modal tab reorganization and validation
  * Execution sequence drag-and-drop
  * Compare tab templates and enhancements
  * Cross-cutting improvements (help tooltips, keyboard shortcuts)
- [ ] Modify `frontend/index.html` - Update HTML structure for:
  * Job catalog search/filter box
  * Settings card visual grouping
  * Job modal tab reorganization
  * Compare tab template dropdowns
  * New buttons and UI elements as described
- [ ] Modify `frontend/styles.css` - Add styles for:
  * New UI states (template indicators, validation feedback)
  * Tooltip styling
  * Enhanced visual grouping
  * Drag-and-drop feedback
  * Consistent icon/button styling

---

## 4. Success Metrics
After implementation, these usability improvements should result in:
- [ ] Reduced time to create a new job (especially from template)
- [ ] Reduced time to set up a common comparison scenario
- [ ] Fewer user errors in configuration (thanks to validation and templates)
- [ ] Improved discoverability of advanced features (through better organization)
- [ ] Increased user satisfaction with common workflows (job creation, comparison setup)
- [ ] Maintained or improved accessibility compliance

---
*Status: Design Complete - Ready for Implementation Planning*
