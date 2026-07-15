"""Behaviour mixins for MainWindow.

MainWindow grew past 1500 lines doing everything at once. These mixins split it
by concern; they are not standalone — each expects to be mixed into MainWindow
and relies on its widgets/state (`self.tree`, `self.seq`, `self.status`, ...).
"""
