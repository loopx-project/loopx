import { useEffect, useState } from 'react';
import {
  fetchLoopxStatus, loopxFailureOf,
  type LoopxStatus, type LoopxStatusFailure,
} from './status';

/** One shared status read for the board metadata and conversation overviews. */
export function useLoopxStatus(token: string | undefined, refreshMs = 15000) {
  const [read, setRead] = useState<{ loading: boolean; status: LoopxStatus | null; failure: LoopxStatusFailure | null; at: number }>(
    { loading: Boolean(token), status: null, failure: null, at: 0 },
  );
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    if (!token) {
      setRead({ loading: false, status: null, failure: null, at: 0 });
      return;
    }
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const readOnce = async (signal: AbortSignal) => {
      try {
        const status = await fetchLoopxStatus({ token, signal });
        if (active) setRead({ loading: false, status, failure: null, at: Date.now() });
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        if (active) setRead({ loading: false, status: null, failure: loopxFailureOf(error), at: Date.now() });
      } finally {
        if (active) timer = setTimeout(() => void readOnce(new AbortController().signal), refreshMs);
      }
    };
    const controller = new AbortController();
    void readOnce(controller.signal);
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [token, refreshMs, nonce]);
  return { ...read, reload: () => setNonce(value => value + 1) };
}

