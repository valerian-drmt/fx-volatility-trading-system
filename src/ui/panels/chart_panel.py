from PyQt5 import QtCore, QtGui
from PyQt5.QtWidgets import QWidget, QGridLayout
import pyqtgraph as pg


class CandlestickItem(pg.GraphicsObject):
    """
    Simple candlestick item for pyqtgraph.
    Data is a list of (x, open, high, low, close).
    """
    def __init__(self, data):
        super().__init__()
        self.data = data
        self.picture = None
        self._generate_picture()

    def set_data(self, data):
        self.data = data
        self._generate_picture()
        self.update()

    def _generate_picture(self):
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        w = 0.6  # candle body width

        for (x, open_, high, low, close) in self.data:
            if close >= open_:
                pen = pg.mkPen('g', width=1)
                brush = pg.mkBrush('g')
            else:
                pen = pg.mkPen('r', width=1)
                brush = pg.mkBrush('r')

            p.setPen(pen)
            p.setBrush(brush)

            # wick
            p.drawLine(QtCore.QPointF(x, low), QtCore.QPointF(x, high))

            # body
            rect = QtCore.QRectF(x - w / 2, open_, w, close - open_)
            p.drawRect(rect.normalized())

        p.end()

    def paint(self, painter, *args):
        if self.picture is not None:
            painter.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        if self.picture is None:
            return QtCore.QRectF()
        r = self.picture.boundingRect()
        return QtCore.QRectF(r.left(), r.top(), r.width(), r.height())


class ChartPanel(QWidget):
    def __init__(self):
        super().__init__()

        layout = QGridLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self.plots = []
        titles = ["Chart 1", "Chart 2", "Chart 3", "Chart 4"]
        for index in range(4):
            plot = pg.PlotWidget()
            plot.setTitle(titles[index])
            plot.setLabel("bottom", "Tick #")
            plot.setLabel("left", "Price")
            self.plots.append(plot)
            layout.addWidget(plot, index // 2, index % 2)

        self.main_plot = self.plots[0]
