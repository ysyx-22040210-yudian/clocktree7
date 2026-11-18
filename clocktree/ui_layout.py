"""Desktop window bounds and shared native ttk styling for Tk 8.5 and newer."""
import tkinter as tk
from tkinter import ttk
from .desktop_art import artwork, glyph


INK = '#203149'
MUTED = '#536681'
ACCENT = '#365ad9'
LINE = '#d9e1eb'
BAR = '#172339'
BOARD = '#101b2b'
SURFACE = '#f3f6fa'


def fit_window(window, parent, preferred, minimum):
    """Fit the client area to the screen and open near the owning window."""
    screen_w, screen_h = window.winfo_screenwidth(), window.winfo_screenheight()
    avail_w, avail_h = max(1, screen_w - 24), max(1, screen_h - 76)
    width, height = min(preferred[0], avail_w), min(preferred[1], avail_h)
    window.minsize(min(minimum[0], width), min(minimum[1], height))
    if parent is not None and parent.winfo_viewable():
        x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
    else:
        x, y = (screen_w - width) // 2, (screen_h - height) // 2
    x = max(0, min(x, screen_w - width - 12))
    y = max(0, min(y, screen_h - height - 54))
    window.geometry('{}x{}+{}+{}'.format(width, height, x, y))


def wrapped_label(parent, **options):
    label = ttk.Label(parent, anchor='w', justify='left', wraplength=400, **options)
    def resize(event):
        width = max(32, event.width - 4)
        if label.winfo_pixels(label.cget('wraplength')) != width:
            label.configure(wraplength=width)
    label.bind('<Configure>', resize)
    return label


def scroll_body(parent, padding=10):
    """A vertically scrolling form with a scrollbar scoped to its own window."""
    outer = ttk.Frame(parent, style='White.TFrame')
    outer.pack(fill='both', expand=True)
    canvas = tk.Canvas(outer, bg='white', highlightthickness=0, width=1, height=1)
    bar = ttk.Scrollbar(outer, command=canvas.yview)
    canvas.configure(yscrollcommand=bar.set)
    bar.pack(side='right', fill='y')
    canvas.pack(fill='both', expand=True)
    body = ttk.Frame(canvas, style='White.TFrame', padding=padding)
    item = canvas.create_window(0, 0, window=body, anchor='nw')
    canvas.bind('<Configure>', lambda e: canvas.itemconfigure(item, width=e.width))
    body.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox(item)))

    def wheel(event):
        if event.widget is canvas or str(event.widget).startswith(str(body) + '.') or event.widget is body:
            up = event.num == 4 or (event.num != 5 and getattr(event, 'delta', 0) > 0)
            canvas.yview_scroll(-2 if up else 2, 'units')
            return 'break'
    window = parent.winfo_toplevel()
    for sequence in ('<MouseWheel>', '<Button-4>', '<Button-5>'):
        window.bind(sequence, wheel, add='+')
    return body


def style_desktop(root):
    family = root.family
    root.option_add('*Font', (family, 10))
    root.option_add('*Menu.Font', (family, 10))
    root.option_add('*Menu.tearOff', False)
    root.option_add('*TCombobox*Listbox.font', (family, 10))
    style = ttk.Style(root)
    style.theme_use('clam')
    art=artwork(root)
    unit=art.scale

    def element(name,base,states=(),border=6):
        def image(key):return art.photo('surface/'+key)
        spec=[tuple(state.split())+(image(key),) for state,key in states]
        style.element_create(name,'image',image(base),*spec,border=round(border*unit),padding=0,sticky='nswe')

    def button(name,base,active,pressed,focused):
        tile='ct.'+name+'.surface'
        element(tile,base,[('disabled','muted'),('pressed',pressed),('focus',focused),('active',active)])
        style.layout(name,[(tile,{'sticky':'nswe','children':[
            ('Button.padding',{'sticky':'nswe','children':[('Button.label',{'sticky':'nswe'})]})]})])

    button('TButton','button','hover','pressed','focus')
    button('Accent.TButton','blue','blue-hover','blue-pressed','blue-focus')
    button('Header.TButton','header-cta','header-cta-hover','blue-pressed','header-focus')
    button('Quiet.TButton','ghost','hover','pressed','focus')
    button('LayerActive.TButton','soft','hover','pressed','focus')
    for name,base in [('Metric.TFrame','card'),('First.Metric.TFrame','first-card')]:
        element(name+'.surface',base)
        style.layout(name,[(name+'.surface',{'sticky':'nswe','children':[('Frame.padding',{'sticky':'nswe'})]})])
    element('ct.tab','tab',[('selected','tab-on'),('active','tab-hover')],4)
    style.layout('TNotebook.Tab',[('ct.tab',{'sticky':'nswe','children':[
        ('Notebook.padding',{'sticky':'nswe','children':[('Notebook.label',{'sticky':'nswe'})]})]})])
    element('ct.field','button',[('disabled','muted'),('focus','focus')])
    element('ct.header.field','header',[('focus','header-focus')])
    style.element_create('ct.arrow','image',glyph(root,'chevron'),sticky='')
    style.element_create('ct.header.arrow','image',glyph(root,'chevron','night'),sticky='')
    style.layout('TEntry',[('ct.field',{'sticky':'nswe','children':[
        ('Entry.padding',{'sticky':'nswe','children':[('Entry.textarea',{'sticky':'nswe'})]})]})])
    for name,tile,arrow in [('TCombobox','ct.field','ct.arrow'),('Header.TCombobox','ct.header.field','ct.header.arrow')]:
        style.layout(name,[(tile,{'sticky':'nswe','children':[
            (arrow,{'side':'right','sticky':'ns'}),('Combobox.padding',{'sticky':'nswe','children':[
                ('Combobox.textarea',{'sticky':'nswe'})]})]})])
    style.layout('TMenubutton',[('ct.field',{'sticky':'nswe','children':[
        ('Menubutton.padding',{'sticky':'nswe','children':[
            ('ct.arrow',{'side':'right'}),('Menubutton.label',{'sticky':'nswe'})]})]})])
    for kind in ('idle','pass','busy','warn'):
        name=kind+'.Status.TLabel'
        element(name+'.surface',kind,border=9)
        style.layout(name,[(name+'.surface',{'sticky':'nswe','children':[
            ('Label.padding',{'sticky':'nswe','children':[('Label.label',{'sticky':'nswe'})]})]})])
        color={'pass':'#9be7cb','warn':'#ffdb94','busy':'#bed4ff','idle':'#c2cfe1'}[kind]
        style.configure(name,foreground=color,background=BAR,font=(family,9),padding=(9,3))
    style.configure('.', font=(family, 10), foreground=INK, background=SURFACE, bordercolor=LINE)
    style.configure('TFrame', background=SURFACE)
    style.configure('White.TFrame', background='white')
    style.configure('TLabel', background=SURFACE, foreground=INK)
    style.configure('White.TLabel', background='white')
    style.configure('Title.TLabel', background='white', font=(family, 14, 'bold'))
    style.configure('Section.TLabel', background='white', font=(family, 11, 'bold'))
    style.configure('Muted.TLabel', background='white', foreground=MUTED, font=(family, 9))
    style.configure('Small.TLabel', background='white', foreground=MUTED, font=(family, 9))
    style.configure('TButton', padding=(9, 4), background='white', foreground=INK, anchor='center',
                    borderwidth=1, relief='flat', focuscolor=ACCENT)
    style.map('TButton', background=[('pressed', '#dcece9'), ('active', '#edf5f3')],
              bordercolor=[('focus', ACCENT), ('active', '#90afa9')], foreground=[('disabled', '#82909b')])
    style.configure('Accent.TButton', background=ACCENT, foreground='white', font=(family, 10, 'bold'),
                    focuscolor='white', padding=(11, 6))
    style.configure('Header.TButton',foreground='white',background=ACCENT,font=(family,10,'bold'),padding=(12,6))
    style.map('Header.TButton',foreground=[('!disabled','white')])
    style.configure('Quiet.TButton',padding=(6,3),foreground=MUTED)
    style.map('Accent.TButton', background=[('disabled', '#b9c7cb'), ('pressed', '#075c56'), ('active', '#08645e')],
              foreground=[('disabled', '#344955'), ('!disabled', 'white')])
    style.configure('Layer.TButton', padding=(3, 4), font=(family, 9, 'bold'))
    style.configure('LayerActive.TButton',padding=(3,4),background='#eaf0ff',foreground='#2945ac',font=(family,9,'bold'))
    style.configure('TMenubutton', background='white', foreground=INK, padding=(7, 4),
                    indicatorwidth=8, indicatorheight=5, indicatormargin=3, arrowcolor=MUTED)
    style.configure('TEntry', fieldbackground='white',padding=(7,4),borderwidth=1,lightcolor=LINE,darkcolor=LINE)
    style.map('TEntry', bordercolor=[('focus', ACCENT)])
    style.configure('TCombobox', fieldbackground='white', background='white', padding=(7,4), arrowsize=10)
    style.map('TCombobox', fieldbackground=[('readonly', 'white')], selectbackground=[('readonly', 'white')],
              selectforeground=[('readonly', INK)], bordercolor=[('focus', ACCENT)])
    style.map('Header.TCombobox',fieldbackground=[('readonly','#263650')],selectbackground=[('readonly','#263650')],
              foreground=[('readonly','#f0f5ff')],selectforeground=[('readonly','#f0f5ff')])
    style.configure('Header.TCombobox',foreground='white',padding=(9,5))
    style.configure('Treeview', background='white', fieldbackground='white', foreground=INK,
                    rowheight=27, borderwidth=0, font=(family, 9))
    style.configure('Treeview.Heading', background='#eef2f8', foreground='#415572', padding=(5, 5),
                    font=(family, 9, 'bold'), relief='flat')
    style.map('Treeview', background=[('selected','#e4ecff')],foreground=[('selected','#243f8c')])
    style.configure('TNotebook', background='white', borderwidth=0, tabmargins=(0, 0, 0, 0))
    style.configure('TNotebook.Tab',padding=(10,7),background='white',foreground=MUTED,borderwidth=0)
    style.map('TNotebook.Tab', foreground=[('selected','#2949b7')])
    style.configure('TCheckbutton',focuscolor=ACCENT,background='white')
    style.configure('Horizontal.TProgressbar', background=ACCENT, troughcolor='#e4ecee', borderwidth=0, thickness=3)
    style.configure('TPanedwindow', background=LINE)
    for axis in ('Horizontal', 'Vertical'):
        style.configure(axis + '.TScrollbar', background='#cedbdd', troughcolor='#f4f7f7', borderwidth=0, arrowsize=11)


def panel_heading(parent,title,icon):
    row=ttk.Frame(parent,padding=(14,8),style='White.TFrame')
    row.pack(fill='x')
    ttk.Label(row,image=glyph(row,icon,'blue'),style='White.TLabel').pack(side='left',padx=(0,8))
    ttk.Label(row,text=title,style='Section.TLabel').pack(side='left')


def dialog_heading(parent,title,icon,detail=''):
    row=tk.Frame(parent,bg=BAR,padx=16,pady=9)
    row.pack(fill='x')
    family=getattr(parent._root(),'family','TkDefaultFont')
    tk.Label(row,image=glyph(row,icon,'night'),bg=BAR,bd=0).pack(side='left',padx=(0,9))
    tk.Label(row,text=title,font=(family,13,'bold'),fg='#f2f6ff',bg=BAR,bd=0).pack(side='left')
    if detail:
        caption=tk.Label(row,text=detail,anchor='w',font=(family,9),fg='#becbe2',bg=BAR,bd=0)
        caption.pack(side='left',fill='x',expand=True,padx=(14,0))
