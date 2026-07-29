/**
 * ============================================================================
 * assets/login.js — 登入頁行為邏輯
 * ============================================================================
 * 前端不包含任何帳號、密碼、雜湊或 secret，僅將使用者輸入的帳密以 POST
 * 送到 /api/login，並依回應顯示錯誤訊息或導向原本要瀏覽的頁面。
 * ============================================================================
 */
(function () {
  'use strict';

  const form = document.getElementById('login-form');
  const usernameInput = document.getElementById('login-username');
  const passwordInput = document.getElementById('login-password');
  const toggleBtn = document.getElementById('login-toggle-pass');
  const submitBtn = document.getElementById('login-submit-btn');
  const submitLabel = document.getElementById('login-submit-label');
  const errorBox = document.getElementById('login-error');

  // 只允許導向站內相對路徑（開頭為單一個 "/"），避免開放重導向（open redirect）漏洞
  function getSafeRedirectTarget() {
    try {
      const params = new URLSearchParams(window.location.search);
      const target = params.get('redirect');
      const hasBackslash = target ? target.indexOf(String.fromCharCode(92)) !== -1 : false;
      if (target && target.startsWith('/') && !target.startsWith('//') && !hasBackslash) {
        return target;
      }
    } catch (e) { /* noop */ }
    return '/';
  }

  function showError(message) {
    errorBox.textContent = message;
    errorBox.classList.add('show');
  }

  function hideError() {
    errorBox.textContent = '';
    errorBox.classList.remove('show');
  }

  function setLoading(isLoading) {
    submitBtn.disabled = isLoading;
    submitLabel.textContent = isLoading ? '驗證中…' : 'Sign In';
  }

  if (toggleBtn && passwordInput) {
    toggleBtn.addEventListener('click', function () {
      const isHidden = passwordInput.type === 'password';
      passwordInput.type = isHidden ? 'text' : 'password';
      toggleBtn.setAttribute('aria-label', isHidden ? '隱藏密碼' : '顯示密碼');
      toggleBtn.setAttribute('aria-pressed', String(isHidden));
      toggleBtn.querySelector('[data-eye-open]').style.display = isHidden ? 'none' : 'block';
      toggleBtn.querySelector('[data-eye-closed]').style.display = isHidden ? 'block' : 'none';
    });
  }

  form.addEventListener('submit', async function (e) {
    e.preventDefault();
    hideError();

    const username = usernameInput.value.trim();
    const password = passwordInput.value;

    if (!username || !password) {
      showError('請輸入帳號與密碼。');
      return;
    }

    setLoading(true);
    try {
      const resp = await fetch('/api/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ username, password })
      });

      let data = {};
      try { data = await resp.json(); } catch (e) { data = {}; }

      if (resp.ok && data.ok) {
        window.location.href = getSafeRedirectTarget();
        return;
      }

      if (resp.status === 401) {
        showError('帳號或密碼錯誤，請重新輸入。');
      } else if (resp.status === 429) {
        showError('嘗試次數過多，請稍待片刻後再試。');
      } else if (resp.status === 500) {
        showError((data && data.error) || '伺服器未設定登入憑證，請聯絡管理員。');
      } else {
        showError('登入失敗，請稍後再試。');
      }
    } catch (err) {
      showError('無法連線到伺服器，請確認網路連線後再試一次。');
    } finally {
      setLoading(false);
    }
  });

  // 自動 focus 帳號欄
  usernameInput.focus();
})();
