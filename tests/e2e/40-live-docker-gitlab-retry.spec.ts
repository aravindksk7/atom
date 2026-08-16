import { test, expect } from './fixtures';

test.describe('GitLab CI Integration and Retry', () => {
  test('renders GitLab CI modal with snippet and retry options', async ({ authedPage }) => {
    await authedPage.goto('/#launch');
    await expect(authedPage.locator('#btn-gitlab-ci-snippet')).toBeVisible();
    await authedPage.click('#btn-gitlab-ci-snippet');
    await expect(authedPage.locator('.gitlab-ci-modal')).toBeVisible();
    await expect(authedPage.locator('.gitlab-ci-snippet-code')).toContainText('run-atom-selection.sh');
  });
});
