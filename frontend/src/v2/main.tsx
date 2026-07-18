import ReactDOM from 'react-dom/client';
import { useStore } from '../state/store';
import App from './App';
import './app.css';

// Boot the shared viewer store straight into the injection-molding wall
// thickness view — the check a production engineer nearly always starts from.
useStore.getState().set({ processId: 'injection_molding', modeId: 'thickness' });

// The viewer controller is an imperative singleton; StrictMode's double-mount
// would spin up two WebGL contexts, so this entry renders without it.
ReactDOM.createRoot(document.getElementById('root')!).render(<App />);
