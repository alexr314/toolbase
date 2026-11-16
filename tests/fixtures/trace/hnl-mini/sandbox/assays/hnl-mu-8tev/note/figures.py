# %% [markdown]
# # Figures: heavy Majorana neutrino with muon mixing, 8 TeV
# Each figure reads records only (`../records`, `../checks/xsec`); nothing here simulates.

# %%
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator

def xticks(ax):
    ax.xaxis.set_major_locator(FixedLocator([40, 50, 75, 100, 200, 300, 500]))
    ax.xaxis.set_minor_locator(NullLocator())
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, p: f'{v:g}'))

from phenomaton import figures
figures.use()
REC = Path('../records')
B = json.loads((REC / 'build.json').read_text())
S = json.loads((REC / 'sensitivity.json').read_text())['points']
X = json.loads(Path('../checks/xsec/result.json').read_text())['results']
P = json.loads((REC / 'paper_estimate.json').read_text())
g = B['grid_mN_GeV']

# %% [markdown]
# ## Figure 1: branching ratios
# Charged-current hadronic, charged-current leptonic and the rest, against mN (both charges summed),
# Reads `records/build.json`.

# %%
grp = B['BR_groups_by_mN']
had = B['BR_CC_hadronic_both_charges']
lep = [sum(v for k, v in grp[str(m)].items() if k.startswith('CC_leptonic')) for m in g]
oth = [1 - h - l for h, l in zip(had, lep)]
fig, ax = plt.subplots(figsize=(6.0, 3.4))
ax.plot(g, had, 'C0-o', ms=3, label=r'$\mu^\pm q\bar{q}^{\prime}$ (both charges)')
ax.plot(g, lep, 'C1-o', ms=3, label=r'$\mu^\pm \ell\nu$ (CC leptonic)')
ax.plot(g, oth, 'C2-o', ms=3, label=r'neutral current and mixed')
ax.axvline(79.82, color='0.6', lw=0.8, ls=':')
ax.set(xscale='log', xlabel=r'$m_N$ [GeV]', ylabel='branching ratio', ylim=(0, 0.8))
xticks(ax)
ax.legend(loc='upper right', fontsize=10, ncol=2)
figures.save(fig, 'figures/br')

# %% [markdown]
# ## Figure 2: production cross section and same-sign rate
# sigma_ref(mN) and sigma x BR_ss at |V|^2 = 1, with the independent Drell-Yan reweighting check
# overlaid at 50-500 GeV. Reads `records/build.json`, `checks/xsec/result.json`.

# %%
fig, ax = plt.subplots(figsize=(6.0, 3.6))
ax.plot(g, B['sigma_total_pb'], 'k-o', ms=3, label=r'$\sigma_{\rm ref}$ (MadGraph, LO)')
ax.fill_between(g, B['sigma_total_scale_down_pb'], B['sigma_total_scale_up_pb'], color='k', alpha=0.15, lw=0)
ax.plot(g, B['sigma_x_BR_samesign_pb'], 'C0-s', ms=3, label=r'$\sigma_{\rm ref}\,\mathrm{BR}(N\to\mu^\pm q\bar{q}^{\prime})$')
xm = [int(k) for k in X]
ax.plot(xm, [X[k]['sigma_pb'] for k in X], 'C3x', ms=8, mew=1.5, ls='none', label=r'independent check of $\sigma_{\rm ref}$')
ax.set(xscale='log', yscale='log', xlabel=r'$m_N$ [GeV]', ylabel=r'cross section at $|V_{\mu N}|^2=1$ [pb]')
xticks(ax)
ax.legend(loc='lower left', fontsize=10)
figures.save(fig, 'figures/xsec')

# %% [markdown]
# ## Figure 3: expected limit
# Median expected 95% CL limit on |V|^2 with +-1, 2 sigma bands, the 15%/50% background-systematic and
# non-prompt x0.5/x2 variations, and the paper estimate. Reads `records/sensitivity.json`, `records/paper_estimate.json`.

# %%
ms = sorted(int(k) for k in S)
L = lambda key: [S[str(m)]['limit_V2'][key] for m in ms]
V = lambda key: [S[str(m)]['limit_V2_variations'][key] for m in ms]
fig, (ax, ratios) = plt.subplots(2, 1, figsize=(6.0, 5.6), sharex=True, gridspec_kw={'height_ratios': [2, 1]})
ax.fill_between(ms, L('-2sigma'), L('+2sigma'), color='gold', alpha=0.6, lw=0, label=r'$\pm2\sigma$')
ax.fill_between(ms, L('-1sigma'), L('+1sigma'), color='limegreen', alpha=0.6, lw=0, label=r'$\pm1\sigma$')
ax.plot(ms, L('expected'), 'k-o', ms=3, label='expected')
ratios.plot(ms, [v/e for v,e in zip(V('bkg15'), L('expected'))], 'C0--', lw=1, label=r'bkg syst. 15%')
ratios.plot(ms, [v/e for v,e in zip(V('bkg50'), L('expected'))], 'C0:', lw=1.4, label=r'bkg syst. 50%')
ratios.plot(ms, [v/e for v,e in zip(V('np_x0.5'), L('expected'))], 'C3--', lw=1, label=r'non-prompt $\times0.5$')
ratios.plot(ms, [v/e for v,e in zip(V('np_x2'), L('expected'))], 'C3:', lw=1.4, label=r'non-prompt $\times2$')
pm = sorted(int(k) for k in P)
ax.plot(pm, [P[str(m)]['V2_lim'] for m in pm], 'm*', ms=9, ls='none', label='back-of-envelope')
ax.set(xscale='log', yscale='log', xlabel=r'$m_N$ [GeV]', ylabel=r'expected 95% CL limit on $|V_{\mu N}|^2$')
xticks(ax)
ax.set_xlabel('')
ax.legend(loc='upper left', fontsize=9, ncol=2)
ratios.axhline(1, color='0.5', lw=0.7)
ratios.set(xscale='log', xlabel=r'$m_N$ [GeV]', ylabel='limit / nominal', ylim=(0.5, 2.2))
xticks(ratios)
ratios.legend(loc='upper right', fontsize=8, ncol=2)
figures.save(fig, 'figures/limit_curve')
