---
name: html-form-submit-button-name-clobber
description: Diagnose and fix form submission failures when button is named 'submit'
pattern_type: error_resolution
learned: true
learned_at: 2026-08-25T07:41:40
source_session: 17132ff0-0e62-4b3d-b504-f1add87835f5
---

## When to use

You're automating form submission with Playwright, Selenium, or similar tools, and the form submits but the server rejects it with validation errors or 'missing field' messages. First check: does the form contain `<button type="submit" name="submit">`?

## How

HTML buttons with `name="submit"` clobber the form's native `submit()` method in the DOM. When automation code calls `form.submit()`, it actually invokes the button object instead, breaking form submission and excluding the button's name/value from POST data.

**Diagnosis:**
- Submit the form manually and inspect Network tab for POST body — look for `submit=<value>` field
- Save HTML before/after and compare (use `page.content()` or DevTools > Save as HTML)
- Check if automation calls `form.submit()` vs `button.click()`

**Fix:** Click the button instead of calling form.submit():
```javascript
// Bad: form.submit is shadowed by button object
form.submit()

// Good: button.click() triggers native browser form submission
button.click()
```

## Example

Captcha form HTML:
```html
<form method="post">
  <button type="submit" name="submit" value="Get Link">Get Link</button>
</form>
```

Playwright (site2s case):
```python
# Old: server returned {status:'error'} — 'submit' field missing from POST
page.evaluate("() => { document.querySelector('form').submit() }")

# Fixed: server now receives submit=Get Link in POST
page.evaluate("() => { document.querySelector('button[type=submit][name=submit]').click() }")
```

Root cause: button's DOM property shadows `HTMLFormElement.prototype.submit`. Clicking the button restores normal browser form submission pipeline, including the button's name/value pair.
