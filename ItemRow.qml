import QtQuick
import QtQuick.Layouts
import qs.Commons
import qs.Ui

// One row: checkbox, name, details, tag, size, optional "Open folder".
// Every string is shown as plain text: names and paths come from the system.
Item {
  id: row

  property string primary: ""
  property string secondary: ""
  property string tag: ""
  property string sizeText: ""
  property bool checked: false
  property bool showCheck: true
  property bool showOpen: false
  property bool warn: false
  property bool current: false      // keyboard cursor

  signal toggled()
  signal openFolder()

  implicitHeight: layout.implicitHeight + Style.space(8)
  height: implicitHeight

  Rectangle {
    anchors.fill: parent
    color: row.current ? Qt.rgba(Color.accent.r, Color.accent.g, Color.accent.b, 0.14)
      : hover.containsMouse ? Qt.rgba(Color.foreground.r, Color.foreground.g, Color.foreground.b, 0.06) : "transparent"
    border.color: row.current ? Color.accent : "transparent"
    border.width: row.current ? 1 : 0
    radius: Style.cornerRadius
  }

  MouseArea {
    id: hover
    anchors.fill: parent
    hoverEnabled: true
    enabled: row.showCheck
    cursorShape: row.showCheck ? Qt.PointingHandCursor : Qt.ArrowCursor
    onClicked: row.toggled()
  }

  RowLayout {
    id: layout
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.verticalCenter: parent.verticalCenter
    anchors.leftMargin: Style.space(8)
    anchors.rightMargin: Style.space(8)
    spacing: Style.space(10)

    Text {
      visible: row.showCheck
      text: row.checked ? "󰄲" : "󰄱"
      textFormat: Text.PlainText
      color: row.checked ? Color.accent : Color.muted
      font.family: Style.font.family
      font.pixelSize: Style.font.heading
    }

    ColumnLayout {
      Layout.fillWidth: true
      spacing: 0

      Text {
        Layout.fillWidth: true
        text: row.primary
        textFormat: Text.PlainText
        elide: Text.ElideMiddle
        color: row.warn ? Color.urgent : Color.foreground
        font.family: Style.font.family
        font.pixelSize: Style.font.body
      }

      Text {
        Layout.fillWidth: true
        visible: row.secondary !== ""
        text: row.secondary
        textFormat: Text.PlainText
        elide: Text.ElideRight
        color: Color.muted
        font.family: Style.font.family
        font.pixelSize: Style.font.bodySmall
      }
    }

    Text {
      visible: row.tag !== ""
      text: row.tag
      textFormat: Text.PlainText
      color: row.tag === "maybe" ? Color.urgent : Color.muted
      font.family: Style.font.family
      font.pixelSize: Style.font.bodySmall
    }

    Text {
      visible: row.sizeText !== ""
      text: row.sizeText
      textFormat: Text.PlainText
      color: Color.muted
      font.family: Style.font.family
      font.pixelSize: Style.font.bodySmall
      Layout.preferredWidth: Style.space(72)
      horizontalAlignment: Text.AlignRight
    }

    Button {
      visible: row.showOpen
      iconText: "󰉋"
      tooltipText: "Open folder"
      onClicked: row.openFolder()
    }
  }
}
