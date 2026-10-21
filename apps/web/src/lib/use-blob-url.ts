import { useEffect, useMemo } from 'react';

/**
 * 把一个 Blob 变成可渲染的 object URL,并在 Blob 变化/组件卸载时 revoke。
 *
 * 图片预览必须这么做:后端下载端点是带鉴权的二进制接口,不能直接塞进 `<img src>`。
 */
export function useBlobUrl(blob: Blob | null | undefined): string | null {
  const url = useMemo(() => {
    if (!blob || typeof URL.createObjectURL !== 'function') return null;
    return URL.createObjectURL(blob);
  }, [blob]);

  useEffect(() => {
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [url]);

  return url;
}
