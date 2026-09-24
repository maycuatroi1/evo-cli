---
name: html-form-submit-button-name-clobber
description: Diagnose and fix form submission failures when button is named 'submit'
learned: true
pattern_type: error_resolution
learned_at: 2026-08-25T07:41:40
source_session: 17132ff0-0e62-4b3d-b504-f1add87835f5
---

## When to use
Browser automation (Playwright, Selenium) submits a form, but the server answers with a validation error or `{status: 'error'}`. The form contains a control named `submit`, e.g. `<button type="submit" name="submit" value="Get Link">`.

## Why
1. A form control named `submit` shadows the method: `form.submit` is the button element, so `form.submit()` throws `TypeError: form.submit is not a function`.
2. Calling the native method via `HTMLFormElement.prototype.submit.call(form)` does submit, but `submit()` never has a submitter. The button's `name=value` pair (`submit=Get Link`) is missing from the POST body, and servers that check for it reject the request. It also skips the `submit` event and validation.

## How
Submit through the button so the browser includes it as the submitter:

```javascript
document.querySelector('button[type=submit][name=submit]').click()
// or: form.requestSubmit(button)
```

With Playwright, prefer `page.click('button[type="submit"][name="submit"]')`. evo-cli's `site2s` (`submit_captcha_form`) does this, with a JS `b.click()` fallback.

## Diagnosis
Compare the POST body of a manual submit (DevTools > Network) with the automated one. Look for the missing `submit=` field.
