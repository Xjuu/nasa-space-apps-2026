#!/usr/bin/env python3
"""Export standalone reliability plots from the two fixed calibration experiments."""
from pathlib import Path
import json
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=Path(__file__).resolve().parents[1]
for stem,label in [('daily_risk_calibration','Starszy, zamrożony blender · test 2025'),
                   ('daily_risk_calibration_current','Bieżący blender · późniejsza część 2025')]:
    report=json.loads((ROOT/'reports'/f'{stem}.json').read_text())
    fig,axes=plt.subplots(1,3,figsize=(12,4.7),sharex=True,sharey=True)
    for h,ax in enumerate(axes,1):
        row=report['horizons'][str(h)]
        ax.plot([0,1],[0,1],color='#777777',linestyle='--',linewidth=1,label='Idealna kalibracja')
        for key,name,color in [('raw_frozen','Przed kalibracją','#0072B2'),('calibrated','Po kalibracji','#D55E00')]:
            bins=[b for b in row[key]['reliability'] if b['cells']]
            ax.plot([b['mean_probability'] for b in bins],[b['observed_frequency'] for b in bins],
                    marker='o',markersize=4,color=color,label=name,linewidth=1.4)
        ax.set_title(f"Do końca dnia {h}\nECE: {100*row['raw_frozen']['ece']:.2f}% → {100*row['calibrated']['ece']:.2f}%",fontsize=11)
        ax.set_xlim(0,1);ax.set_ylim(0,1);ax.set_xticks([0,.25,.5,.75,1]);ax.grid(alpha=.18)
        ax.set_xlabel('Prognozowane ryzyko komórki')
    axes[0].set_ylabel('Odsetek nowych detekcji VIIRS')
    fig.suptitle('Kalibracja ML + fizyka — '+label,fontsize=13,y=.99)
    handles,labels=axes[0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='lower center',ncol=3,bbox_to_anchor=(.5,.065),frameon=False)
    fig.text(.5,.015,f"Kalibracja: {report['n_calibration_cases']} przypadków · test: {report['n_test_cases']} przypadków · ryzyko skumulowane · benchmark retrospektywny",
             ha='center',fontsize=9,color='#444444')
    fig.subplots_adjust(left=.075,right=.98,top=.79,bottom=.25,wspace=.18)
    for ext in ['png','svg']:fig.savefig(ROOT/'reports'/f'{stem}_reliability.{ext}',dpi=180)
    plt.close(fig)
    print(stem,'plots saved')
