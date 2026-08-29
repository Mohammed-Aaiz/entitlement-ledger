import { createContext, useContext, useState, useEffect, type ReactNode } from 'react';

interface User {
  user_id: string;
  email: string;
  display_name: string;
  role: string;
  tenant_id: string;
}

interface AuthState {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, displayName: string, tenantName: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthState | null>(null);

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(() => localStorage.getItem('el_token'));
  const [loading, setLoading] = useState(true);

  // Validate token on mount
  useEffect(() => {
    if (!token) { setLoading(false); return; }
    fetch('/api/auth/me', { headers: { Authorization: `Bearer ${token}` } })
      .then((r) => {
        if (!r.ok) throw new Error('invalid');
        return r.json();
      })
      .then((data: User) => setUser(data))
      .catch(() => { localStorage.removeItem('el_token'); setToken(null); setUser(null); })
      .finally(() => setLoading(false));
  }, [token]);

  const login = async (email: string, password: string) => {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: '' }));
      const msg = err.detail || '';
      switch (res.status) {
        case 401: throw new Error(msg || 'Invalid email or password');
        case 403: throw new Error(msg || 'Account deactivated');
        case 404: throw new Error('Auth endpoint not found — is the backend running?');
        case 422: throw new Error(msg || 'Invalid input — check your email and password');
        case 500: throw new Error('Server error — please try again later');
        case 503: throw new Error('Service unavailable — backend may be starting up');
        default: throw new Error(msg || `Login failed (${res.status})`);
      }
    }
    const data = await res.json();
    localStorage.setItem('el_token', data.access_token);
    setToken(data.access_token);
    setUser({ user_id: data.user_id, email: data.email, display_name: data.display_name,
              role: data.role, tenant_id: data.tenant_id });
  };

  const register = async (email: string, password: string, displayName: string, tenantName: string) => {
    const res = await fetch('/api/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password, display_name: displayName, tenant_name: tenantName }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: '' }));
      const msg = err.detail || '';
      switch (res.status) {
        case 409: throw new Error(msg || 'Email already registered');
        case 422: throw new Error(msg || 'Invalid input — check all required fields');
        case 500: throw new Error('Server error — please try again later');
        default: throw new Error(msg || `Registration failed (${res.status})`);
      }
    }
    const data = await res.json();
    localStorage.setItem('el_token', data.access_token);
    setToken(data.access_token);
    setUser({ user_id: data.user_id, email: data.email, display_name: data.display_name,
              role: data.role, tenant_id: data.tenant_id });
  };

  const logout = () => {
    localStorage.removeItem('el_token');
    setToken(null);
    setUser(null);
  };

  return (
    <AuthContext.Provider value={{ user, token, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}
