import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { api, setSessionExpiredHandler } from "../api/client";
import { clearTokens, readTokens, writeTokens } from "../api/tokens";
import type { TokenPair, User } from "../api/types";

interface AuthState {
  user: User | null;
  isAuthenticated: boolean;
  /** True until the stored session has been checked, so routes don't flash. */
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  forgetSession: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isAuthenticated, setAuthenticated] = useState(false);
  const [isLoading, setLoading] = useState(true);

  const forgetSession = useCallback(() => {
    clearTokens();
    setUser(null);
    setAuthenticated(false);
  }, []);

  useEffect(() => {
    setSessionExpiredHandler(forgetSession);
    // A stored token pair is treated as a session; the first protected request
    // settles whether it is still good.
    setAuthenticated(readTokens() !== null);
    setLoading(false);
  }, [forgetSession]);

  const login = useCallback(async (email: string, password: string) => {
    const tokens = await api.post<TokenPair>("/auth/login", { email, password }, false);
    writeTokens({ accessToken: tokens.access_token, refreshToken: tokens.refresh_token });
    setAuthenticated(true);
  }, []);

  const register = useCallback(async (email: string, password: string) => {
    const result = await api.post<{ user: User; tokens: TokenPair }>(
      "/auth/register",
      { email, password, tos_accepted: true },
      false,
    );
    writeTokens({
      accessToken: result.tokens.access_token,
      refreshToken: result.tokens.refresh_token,
    });
    setUser(result.user);
    setAuthenticated(true);
  }, []);

  const logout = useCallback(async () => {
    const stored = readTokens();
    if (stored) {
      // Best effort: the local session is cleared regardless, so a network
      // failure never leaves someone stuck signed in.
      try {
        await api.post("/auth/logout", { refresh_token: stored.refreshToken }, false);
      } catch {
        /* ignored */
      }
    }
    forgetSession();
  }, [forgetSession]);

  const value = useMemo(
    () => ({ user, isAuthenticated, isLoading, login, register, logout, forgetSession }),
    [user, isAuthenticated, isLoading, login, register, logout, forgetSession],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside an AuthProvider.");
  return context;
}
