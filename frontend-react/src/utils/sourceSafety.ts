// 只允许浏览器直接访问的 HTTP(S) 来源进入页面 URL 和可见文本。
export function browserHttpSource(value?: string | null) {
  const source = (value || '').trim().replace(/^<|>$/g, '');
  if (!source) return null;
  try {
    const url = new URL(source);
    return url.protocol === 'http:' || url.protocol === 'https:' ? source : null;
  } catch {
    return null;
  }
}

// 来源标签只展示公开地址主机；内部路径统一隐藏。
export function describeBrowserSource(...values: Array<string | null>) {
  const publicSource = values.map((value) => browserHttpSource(value)).find(Boolean);
  if (publicSource) {
    return `公开来源 · ${new URL(publicSource).host}`;
  }
  return values.some((value) => Boolean((value || '').trim())) ? '受控内部来源 · 路径已隐藏' : null;
}
