const $ = (selector) => document.querySelector(selector);
let snapshot, setup, busy = false, accountSaved = false, egressChecked = false, authenticated = false;
let publicKey = '', detectedProxies = [], testedProxy = null;

function message(text, error = false) {
  $('#setup-message').textContent = text;
  $('#setup-message').classList.toggle('error', error);
  if (error) $('#setup-message').scrollIntoView({behavior: 'smooth', block: 'center'});
}

async function request(path, body) {
  const options = body === undefined ? {cache: 'no-store'} : {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Workbench-Token': snapshot.token},
    body: JSON.stringify(body),
  };
  const response = await fetch('/api/' + path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '操作未完成，请重试。');
  return data;
}

function controls() {
  const unavailable = busy || !snapshot || snapshot.busy;
  $('#account-fields').disabled = unavailable || !setup?.runtime?.oci;
  $('#network-fields').disabled = unavailable;
  $('#keep-account').disabled = unavailable;
  $('#change-account').disabled = unavailable;
  $('#network-next').disabled = unavailable || !egressChecked;
  $('#check-account').disabled = unavailable || !accountSaved || !egressChecked;
  $('#finish-setup').disabled = unavailable || !authenticated;
  document.querySelectorAll('[data-step]').forEach(button => {
    const step = Number(button.dataset.step);
    button.disabled = unavailable || (step === 2 && !accountSaved) || (step === 3 && !egressChecked);
  });
  document.querySelectorAll('[data-back]').forEach(button => button.disabled = unavailable);
}

async function work(label, action) {
  if (busy) return;
  busy = true; controls(); message(label);
  try { await action(); }
  catch (error) { message(error.message || '无法连接本机工作台，请确认应用仍在运行。', true); }
  finally { busy = false; controls(); }
}

function showStep(step, focus = true) {
  document.querySelectorAll('.setup-panel').forEach(panel => panel.hidden = panel.id !== 'step-' + step);
  document.querySelectorAll('[data-step]').forEach(button => {
    if (Number(button.dataset.step) === step) button.setAttribute('aria-current', 'step');
    else button.removeAttribute('aria-current');
  });
  const panel = $('#step-' + step);
  if (focus) panel.querySelector('h2').focus({preventScroll: true});
  if (window.gsap && !matchMedia('(prefers-reduced-motion: reduce)').matches) {
    gsap.fromTo(panel, {opacity: .4, y: 8}, {opacity: 1, y: 0, duration: .3, clearProps: 'all'});
  }
}

function showPublicKey(key, fingerprint) {
  publicKey = key || '';
  $('#public-key-area').hidden = !publicKey;
  $('#api-public-key').value = publicKey;
  $('#key-fingerprint').textContent = fingerprint ? '指纹 ' + fingerprint : '';
  $('#generate-key').textContent = publicKey ? '使用已生成的公钥' : '生成 API 公钥';
}

function networkProxy() {
  const choice = $('#network-choice').value;
  return choice === 'direct' ? '' : choice === 'manual' ? $('#manual-proxy').value.trim() : detectedProxies[Number(choice.slice(6))]?.url || '';
}

function networkChanged() {
  $('#manual-proxy-label').hidden = $('#network-choice').value !== 'manual';
  egressChecked = testedProxy !== null && testedProxy === networkProxy();
  authenticated = false;
  $('#setup-success').hidden = true;
  $('#summary-region').textContent = '等待核验';
  $('#setup-egress').hidden = !egressChecked;
  controls();
}

function showEgress(data) {
  $('#setup-egress').hidden = false;
  $('#setup-egress-ip').textContent = data.ip;
  $('#setup-egress-location').textContent = [data.country, data.city, data.org].filter(Boolean).join(' · ') || '地区未知';
  $('#setup-egress-time').textContent = '检测时间 ' + new Date(data.checked_at * 1000).toLocaleString('zh-CN', {timeZoneName: 'short', hour12: false});
  $('#summary-egress').textContent = data.ip + (data.country ? ' · ' + data.country : '');
}

$('#generate-key').addEventListener('click', () => work('正在本机准备 API 密钥…', async () => {
  const result = await request('setup/key', {});
  showPublicKey(result.public_key, result.fingerprint);
  message('公钥已准备好。复制后添加到 Oracle，再粘贴配置预览。');
}));

$('#copy-key').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(publicKey); message('公钥已复制，可以添加到 Oracle。'); }
  catch { $('#api-public-key').focus(); $('#api-public-key').select(); message('浏览器未允许自动复制，公钥已选中，请按 Ctrl+C 或 Command+C。'); }
});

document.querySelectorAll('[name="key-mode"]').forEach(input => input.addEventListener('change', () => {
  const imported = $('[name="key-mode"]:checked').value === 'import';
  $('#generate-key-panel').hidden = imported;
  $('#import-key-panel').hidden = !imported;
  $('#private-key-file').required = imported;
  $('#preview-number').textContent = imported ? '2' : '3';
  $('#private-key-file').value = '';
}));

$('#account-form').addEventListener('submit', event => {
  event.preventDefault();
  if (!event.target.reportValidity()) return;
  work('正在保存账户并准备登录密钥…', async () => {
    const imported = $('[name="key-mode"]:checked').value === 'import';
    if (!imported && !publicKey) throw new Error('请先生成 API 公钥，并将它添加到 Oracle。');
    const payload = {config_preview: $('#config-preview').value.trim()};
    if (imported) {
      const file = $('#private-key-file').files[0];
      if (!file) throw new Error('请选择与此 API key 匹配的私钥 .pem 文件。');
      if (file.size > 16384) throw new Error('这个文件过大。请选择单个 API 私钥 .pem 文件。');
      payload.private_key = await file.text();
    }
    let result;
    try { result = await request('setup/account', payload); }
    finally { delete payload.private_key; }
    snapshot.config = result.config;
    $('#private-key-file').value = '';
    $('#config-preview').value = '';
    accountSaved = true; authenticated = false; egressChecked = false; testedProxy = null;
    $('#existing-account').hidden = false;
    $('#account-form').hidden = true;
    $('#summary-account').textContent = '已保存到本机';
    message(result.message || '账户已保存。接下来检测网络。');
    showStep(2);
  });
});

$('#keep-account').addEventListener('click', () => { message('继续使用现有配置。可以选择保留当前网络设置。'); showStep(2); });
$('#change-account').addEventListener('click', () => {
  $('#existing-account').hidden = true; $('#account-form').hidden = false;
  message('准备新的账户配置。点击保存前，现有配置仍会保留。');
});
$('#network-choice').addEventListener('change', networkChanged);
$('#manual-proxy').addEventListener('input', networkChanged);

$('#check-network').addEventListener('click', () => work('正在保存连接方式，并检测实际出口…', async () => {
  const proxy = networkProxy();
  if ($('#network-choice').value === 'manual' && !proxy) throw new Error('请填写代理地址，或选择直接连接。');
  egressChecked = false; authenticated = false; testedProxy = null;
  $('#setup-egress').hidden = true; $('#setup-success').hidden = true; $('#summary-region').textContent = '等待核验';
  snapshot = await request('save', {...snapshot.config, proxy_url: proxy});
  const result = await request('egress', snapshot.config);
  showEgress(result); egressChecked = true; testedProxy = proxy;
  message('出口检测完成。继续核验 Oracle 账户与区域。');
}));

$('#network-next').addEventListener('click', () => { message('最后进行只读核验，确认 Oracle 能识别这份账户配置。'); showStep(3); });
$('#check-account').addEventListener('click', () => work('正在连接 Oracle 并查询 Home Region，请稍候…', async () => {
  authenticated = false; $('#setup-success').hidden = true;
  const result = await request('setup/auth', {});
  if (!result.ok || !result.region) throw new Error('账户核验未返回区域，请重新核验。');
  authenticated = true;
  $('#summary-region').textContent = result.region;
  $('#setup-success').hidden = false;
  message(result.message || '账户连接成功，可以进入工作台。');
}));
$('#finish-setup').addEventListener('click', () => { if (authenticated) location.assign('/#overview'); });
document.querySelectorAll('[data-step]').forEach(button => button.addEventListener('click', () => showStep(Number(button.dataset.step))));
document.querySelectorAll('[data-back]').forEach(button => button.addEventListener('click', () => showStep(Number(button.dataset.back))));

async function load() {
  try {
    [snapshot, setup] = await Promise.all([request('state'), request('setup')]);
    accountSaved = Boolean(setup.runtime.config && setup.runtime.api_key && setup.runtime.ssh_key);
    $('#existing-account').hidden = !accountSaved;
    $('#account-form').hidden = accountSaved;
    $('#runtime-missing').hidden = Boolean(setup.runtime.oci);
    $('#setup-platform').textContent = [setup.runtime.platform, setup.runtime.backend === 'wsl' ? '兼容模式' : '本机运行'].filter(Boolean).join(' · ');
    $('#data-location').textContent = setup.data_dir || '系统用户数据目录';
    showPublicKey(setup.api_public_key, setup.fingerprint);
    const currentProxy = snapshot.config.proxy_url || '';
    $('#manual-proxy').value = currentProxy;
    if (currentProxy) $('#network-choice').value = 'manual';
    networkChanged();
    message(snapshot.busy ? '工作台正在执行任务。请等待任务结束后再修改账户或网络。' : accountSaved ? '已找到现有配置，可以直接继续。' : '先连接自己的 Oracle 账户。所有凭据只保存在本机。');
    try {
      const result = await request('proxies');
      detectedProxies = result.proxies || [];
      detectedProxies.forEach((proxy, index) => {
        const option = document.createElement('option');
        option.value = 'proxy-' + index;
        option.textContent = (proxy.source || '检测到的本机代理') + ' · ' + proxy.url;
        $('#network-choice').insertBefore(option, $('#network-choice option[value=manual]'));
        if (proxy.url === currentProxy) $('#network-choice').value = option.value;
      });
      $('#proxy-detection').textContent = result.message || (detectedProxies.length ? '发现本机代理端口，实际连通性请点击下方按钮检测。' : '未发现常用代理端口，可直接连接或手动填写。');
      networkChanged();
    } catch { $('#proxy-detection').textContent = '自动查找代理未完成，可以直接连接或手动填写。'; }
  } catch (error) { message(error.message || '无法连接本机工作台，请重新打开应用。', true); }
  controls();
}
controls();
load();
