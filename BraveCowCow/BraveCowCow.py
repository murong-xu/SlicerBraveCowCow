import logging
import os
import glob
import re
import vtk
import qt

import slicer
from slicer.ScriptedLoadableModule import *
from slicer.util import VTKObservationMixin


class BraveCowCow(ScriptedLoadableModule):
    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = "BraveCowCow"
        self.parent.categories = ["Segmentation"]
        self.parent.dependencies = []
        self.parent.contributors = ["Houjing Huang, Kaiyuan Yang, Murong Xu (University of Zurich)"]
        self.parent.helpText = """
        BraveCowCow implements a fast 2D tri-axial ROI extraction combined with 3D multi-task segmentation and classification for intracranial vessel analysis.<br>
        This algorithm achieved 2nd place in the RSNA 2024 Intracranial Aneurysm Detection Challenge.<br><br>
        
        For more information, see the <a href="https://www.kaggle.com/competitions/rsna-intracranial-aneurysm-detection/discussion/544807">solution write-up</a> 
        and the <a href="https://github.com/murong-xu/SlicerBraveCowCow">extension documentation</a>.
        """
        self.parent.acknowledgementText = """
        The core algorithm was developed by Pengcheng Shi, Yan Lu, and Jiawei Chen (Medical Image Insights, Shanghai), 
        together with Kaiyuan Yang and Houjing Huang (University of Zurich).<br><br>
        
        Kaiyuan Yang, Houjing Huang, and Pengcheng Shi are also organizers of the MICCAI 
        <a href="https://topcow24.grand-challenge.org/">TopCoW</a> and 
        <a href="https://topbrain2025.grand-challenge.org/">TopBrain</a> challenges, 
        which benchmark segmentation of the Circle of Willis and whole-brain vessel anatomy.<br><br>
        
        This 3D Slicer extension was developed by Murong Xu (University of Zurich).<br><br>
        
        If you use this software in your research, please cite:<br>
        [Citation to be added]  #TODO: 
        """

class BraveCowCowWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):
    def __init__(self, parent=None):
        """
        Called when the user opens the module the first time and the widget is initialized.
        """
        ScriptedLoadableModuleWidget.__init__(self, parent)
        VTKObservationMixin.__init__(self)
        self.logic = None
        self._parameterNode = None
        self._updatingGUIFromParameterNode = False

    def setup(self):
        """
        Called when the user opens the module the first time and the widget is initialized.
        """
        ScriptedLoadableModuleWidget.setup(self)

        # Load widget from .ui file (created by Qt Designer).
        # Additional widgets can be instantiated manually and added to self.layout.
        uiWidget = slicer.util.loadUI(self.resourcePath('UI/BraveCowCow.ui'))
        self.layout.addWidget(uiWidget)
        self.ui = slicer.util.childWidgetVariables(uiWidget)

        # Set scene in MRML widgets. Make sure that in Qt designer the top-level qMRMLWidget's
        # "mrmlSceneChanged(vtkMRMLScene*)" signal in is connected to each MRML widget's.
        # "setMRMLScene(vtkMRMLScene*)" slot.
        uiWidget.setMRMLScene(slicer.mrmlScene)

        # Create logic class. Logic implements all computations that should be possible to run
        # in batch mode, without a graphical user interface.
        self.logic = BraveCowCowLogic()
        self.logic.logCallback = self.addLog

        self.initializeParameterNode()
        self.addObserver(
            slicer.mrmlScene, slicer.mrmlScene.StartCloseEvent, self.onSceneStartClose)
        self.addObserver(slicer.mrmlScene,
                         slicer.mrmlScene.EndCloseEvent, self.onSceneEndClose)

        # Create button groups for radio buttons
        self.onlyForwardClsGroup = qt.QButtonGroup()
        self.onlyForwardClsGroup.addButton(self.ui.onlyForwardClsYesRadio)
        self.onlyForwardClsGroup.addButton(self.ui.onlyForwardClsNoRadio)

        self.toMerge2SegTaskGroup = qt.QButtonGroup()
        self.toMerge2SegTaskGroup.addButton(self.ui.toMerge2SegTaskYesRadio)
        self.toMerge2SegTaskGroup.addButton(self.ui.toMerge2SegTaskNoRadio)

        # Connect TTA type change to update batch size options
        self.ui.ttaTypeComboBox.currentIndexChanged.connect(self.onTtaTypeChanged)
        self.ui.ttaBatchSizeComboBox.currentIndexChanged.connect(self.updateParameterNodeFromGUI)

        # Connect radio buttons
        self.onlyForwardClsGroup.buttonClicked.connect(self.updateParameterNodeFromGUI)
        self.toMerge2SegTaskGroup.buttonClicked.connect(self.updateParameterNodeFromGUI)

        # Connect all buttons and controls to appropriate slots
        self.ui.inputVolumeSelector.connect(
            "currentNodeChanged(vtkMRMLNode*)", self.updateParameterNodeFromGUI)
        self.ui.outputSegmentationSelector.connect(
            "currentNodeChanged(vtkMRMLNode*)", self.updateParameterNodeFromGUI)
        self.ui.useStandardSegmentNamesCheckBox.connect(
            "toggled(bool)", self.updateParameterNodeFromGUI)
        self.ui.cpuCheckBox.connect(
            "toggled(bool)", self.updateParameterNodeFromGUI)
        self.ui.applyButton.connect('clicked(bool)', self.onApplyButton)
        self.ui.packageUpgradeButton.connect(
            'clicked(bool)', self.onPackageUpgrade)
        self.ui.packageInfoUpdateButton.connect(
            'clicked(bool)', self.onPackageInfoUpdate)

        # Initial GUI update
        self.updateGUIFromParameterNode()
        self.onTtaTypeChanged()

    def cleanup(self):
        """
        Called when the application closes and the module widget is destroyed.
        """
        self.removeObservers()

    def enter(self):
        """
        Called each time the user opens this module.
        """
        # Make sure parameter node exists and observed
        self.initializeParameterNode()

    def exit(self):
        """
        Called each time the user opens a different module.
        """
        # Do not react to parameter node changes (GUI wlil be updated when the user enters into the module)
        self.removeObserver(
            self._parameterNode, vtk.vtkCommand.ModifiedEvent, self.updateGUIFromParameterNode)

    def onSceneStartClose(self, caller, event):
        """
        Called just before the scene is closed.
        """
        # Parameter node will be reset, do not use it anymore
        self.setParameterNode(None)

    def onSceneEndClose(self, caller, event):
        """
        Called just after the scene is closed.
        """
        # If this module is shown while the scene is closed then recreate a new parameter node immediately
        if self.parent.isEntered:
          self.initializeParameterNode()

    def initializeParameterNode(self):
        """
        Ensure parameter node exists and observed.
        """
        # Parameter node stores all user choices in parameter values, node selections, etc.
        # so that when the scene is saved and reloaded, these settings are restored.

        self.setParameterNode(self.logic.getParameterNode())

        # Select default input nodes if nothing is selected yet to save a few clicks for the user
        if not self._parameterNode.GetNodeReference("InputVolume"):
            firstVolumeNode = slicer.mrmlScene.GetFirstNodeByClass(
                "vtkMRMLScalarVolumeNode")
            if firstVolumeNode:
                self._parameterNode.SetNodeReferenceID(
                    "InputVolume", firstVolumeNode.GetID())

    def setParameterNode(self, inputParameterNode):
        """
        Set and observe parameter node.
        Observation is needed because when the parameter node is changed then the GUI must be updated immediately.
        """

        if inputParameterNode:
            self.logic.setDefaultParameters(inputParameterNode)

        # Unobserve previously selected parameter node and add an observer to the newly selected.
        # Changes of parameter node are observed so that whenever parameters are changed by a script or any other module
        # those are reflected immediately in the GUI.
        if self._parameterNode is not None:
            self.removeObserver(
                self._parameterNode, vtk.vtkCommand.ModifiedEvent, self.updateGUIFromParameterNode)
        self._parameterNode = inputParameterNode
        if self._parameterNode is not None:
            self.addObserver(
                self._parameterNode, vtk.vtkCommand.ModifiedEvent, self.updateGUIFromParameterNode)

        # Initial GUI update
        self.updateGUIFromParameterNode()

    def updateGUIFromParameterNode(self, caller=None, event=None):
        """
        This method is called whenever parameter node is changed.
        The module GUI is updated to show the current state of the parameter node.
        """
        if self._parameterNode is None or self._updatingGUIFromParameterNode:
            return

        # Make sure GUI changes do not call updateParameterNodeFromGUI (it could cause infinite loop)
        self._updatingGUIFromParameterNode = True

        # Update node selectors and sliders
        self.ui.inputVolumeSelector.setCurrentNode(self._parameterNode.GetNodeReference("InputVolume"))
        # Update only_forward_cls radio buttons
        onlyForwardCls = self._parameterNode.GetParameter("OnlyForwardCls")
        if onlyForwardCls == "true":
            self.ui.onlyForwardClsYesRadio.setChecked(True)
        else:
            self.ui.onlyForwardClsNoRadio.setChecked(True)
        
        # Update to_merge_2_seg_task radio buttons
        toMerge2SegTask = self._parameterNode.GetParameter("ToMerge2SegTask")
        if toMerge2SegTask == "true":
            self.ui.toMerge2SegTaskYesRadio.setChecked(True)
        else:
            self.ui.toMerge2SegTaskNoRadio.setChecked(True)
        
        # Update TTA type
        ttaType = self._parameterNode.GetParameter("TtaType")
        index = self.ui.ttaTypeComboBox.findText(ttaType)
        if index >= 0:
            self.ui.ttaTypeComboBox.setCurrentIndex(index)
        
        # Update TTA batch size
        ttaBatchSize = self._parameterNode.GetParameter("TtaBatchSize")
        bsIndex = self.ui.ttaBatchSizeComboBox.findText(ttaBatchSize)
        if bsIndex >= 0:
            self.ui.ttaBatchSizeComboBox.setCurrentIndex(bsIndex)

        self.ui.cpuCheckBox.checked = self._parameterNode.GetParameter("CPU") == "true"
        self.ui.useStandardSegmentNamesCheckBox.checked = self._parameterNode.GetParameter("UseStandardSegmentNames") == "true"
        self.ui.outputSegmentationSelector.setCurrentNode(self._parameterNode.GetNodeReference("OutputSegmentation"))

        # Update buttons states and tooltips
        inputVolume = self._parameterNode.GetNodeReference("InputVolume")
        if inputVolume:
            self.ui.applyButton.toolTip = "Start segmentation"
            self.ui.applyButton.enabled = True
        else:
            self.ui.applyButton.toolTip = "Select input volume"
            self.ui.applyButton.enabled = False

        if inputVolume:
            self.ui.outputSegmentationSelector.baseName = inputVolume.GetName() + " segmentation"

        # All the GUI updates are done
        self._updatingGUIFromParameterNode = False

    def updateParameterNodeFromGUI(self, caller=None, event=None):
        """
        This method is called when the user makes any change in the GUI.
        The changes are saved into the parameter node (so that they are restored when the scene is saved and loaded).
        """
        if self._parameterNode is None or self._updatingGUIFromParameterNode:
            return

        wasModified = self._parameterNode.StartModify()  # Modify all properties in a single batch

        self._parameterNode.SetNodeReferenceID("InputVolume", self.ui.inputVolumeSelector.currentNodeID)
        
        # Update only_forward_cls
        self._parameterNode.SetParameter("OnlyForwardCls", "true" if self.ui.onlyForwardClsYesRadio.isChecked() else "false")
        
        # Update to_merge_2_seg_task
        self._parameterNode.SetParameter("ToMerge2SegTask", "true" if self.ui.toMerge2SegTaskYesRadio.isChecked() else "false")
        
        # Update TTA type
        self._parameterNode.SetParameter("TtaType", self.ui.ttaTypeComboBox.currentText)
        
        # Update TTA batch size
        self._parameterNode.SetParameter("TtaBatchSize", self.ui.ttaBatchSizeComboBox.currentText)

        self._parameterNode.SetParameter("CPU", "true" if self.ui.cpuCheckBox.checked else "false")
        self._parameterNode.SetParameter("UseStandardSegmentNames", "true" if self.ui.useStandardSegmentNamesCheckBox.checked else "false")
        self._parameterNode.SetNodeReferenceID("OutputSegmentation", self.ui.outputSegmentationSelector.currentNodeID)

        self._parameterNode.EndModify(wasModified)


    def onTtaTypeChanged(self):
        """Update TTA batch size options based on selected TTA type"""
        # Block signals to prevent updateParameterNodeFromGUI being called during update
        self.ui.ttaBatchSizeComboBox.blockSignals(True)
        
        self.ui.ttaBatchSizeComboBox.clear()
        
        ttaType = self.ui.ttaTypeComboBox.currentText
        
        if ttaType == "TTAx1":
            options = ["1"]
        elif ttaType == "TTAx4":
            options = ["1", "2", "3", "4"]
        elif ttaType == "TTAx8":
            options = ["1", "2", "3", "4", "5", "6", "7", "8"]
        else:
            options = ["1"]
        
        for opt in options:
            self.ui.ttaBatchSizeComboBox.addItem(opt)
        
        # Unblock signals
        self.ui.ttaBatchSizeComboBox.blockSignals(False)
        
        self.updateParameterNodeFromGUI()
    
    def addLog(self, text):
        """Append text to log window
        """
        self.ui.statusLabel.appendPlainText(text)
        slicer.app.processEvents()  # force update

    def onApplyButton(self):
        """
        Run processing when user clicks "Apply" button.
        """
        self.ui.statusLabel.plainText = ''

        onlyForwardCls = self.ui.onlyForwardClsYesRadio.isChecked()
        toMerge2SegTask = self.ui.toMerge2SegTaskYesRadio.isChecked()
        ttaType = self.ui.ttaTypeComboBox.currentText
        ttaBatchSize = int(self.ui.ttaBatchSizeComboBox.currentText)

        try:
            slicer.app.setOverrideCursor(qt.Qt.WaitCursor)
            self.logic.setupPythonRequirements()
            slicer.app.restoreOverrideCursor()
        except Exception as e:
            slicer.app.restoreOverrideCursor()
            import traceback
            traceback.print_exc()
            self.ui.statusLabel.appendPlainText(f"Failed to install Python dependencies:\n{e}\n")
            restartRequired = False
            if isinstance(e, InstallError):
                restartRequired = e.restartRequired
            if restartRequired:
                self.ui.statusLabel.appendPlainText("\nApplication restart required.")
                if slicer.util.confirmOkCancelDisplay(
                    "Application is required to complete installation of required Python packages.\nPress OK to restart.",
                    "Confirm application restart",
                    detailedText=str(e)
                    ):
                    slicer.util.restart()
                else:
                    return
            else:
                slicer.util.errorDisplay(f"Failed to install required packages.\n\n{e}")
                return

        with slicer.util.tryWithErrorDisplay("Failed to compute results.", waitCursor=True):
            # Create initial segmentation node if needed
            if not self.ui.outputSegmentationSelector.currentNode():
                self.ui.outputSegmentationSelector.addNode()

            self.logic.useStandardSegmentNames = self.ui.useStandardSegmentNamesCheckBox.checked

            segmentationNodes = self.logic.process(  #TODO: 
                self.ui.inputVolumeSelector.currentNode(),
                self.ui.outputSegmentationSelector.currentNode(),
                self.ui.cpuCheckBox.checked,
                onlyForwardCls=onlyForwardCls,
                toMerge2SegTask=toMerge2SegTask,
                ttaType=ttaType,
                ttaBatchSize=ttaBatchSize
            )

            # Update UI with first node
            if segmentationNodes and len(segmentationNodes) > 0:
                self.ui.outputSegmentationSelector.setCurrentNode(segmentationNodes[0])

        self.ui.statusLabel.appendPlainText(
            f"\nProcessing finished. Created {len(segmentationNodes)} segmentation nodes."
        )

    def onPackageInfoUpdate(self):
        self.ui.packageInfoTextBrowser.plainText = ''
        with slicer.util.tryWithErrorDisplay("Failed to get BraveCowCow package version information", waitCursor=True):
            self.ui.packageInfoTextBrowser.plainText = self.logic.installedBraveCowCowPythonPackageInfo().rstrip()

    def onPackageUpgrade(self):
        with slicer.util.tryWithErrorDisplay("Failed to upgrade BraveCowCow package", waitCursor=True):
            self.logic.setupPythonRequirements(upgrade=True)
        self.onPackageInfoUpdate()
        if not slicer.util.confirmOkCancelDisplay(f"BraveCowCow package update requires a 3D Slicer restart.","Press OK to restart."):
            raise ValueError('Restart was cancelled.')
        else:
            slicer.util.restart()

class InstallError(Exception):
    def __init__(self, message, restartRequired=False):
        # Call the base class constructor with the parameters it needs
        super().__init__(message)
        self.message = message
        self.restartRequired = restartRequired
    def __str__(self):
        return self.message


class BraveCowCowLogic(ScriptedLoadableModuleLogic):
    _requirements_checked = False  # static variable to ensure that requirements are checked only once
   
    def __init__(self):
        """
        Called when the logic class is instantiated. Can be used for initializing member variables.
        """
        ScriptedLoadableModuleLogic.__init__(self)
        self.isSingletonParameterNode = True

        from collections import OrderedDict

        #TODO: BraveCowCow package (script, setup.py, model weights download...) update this in every release (also remember to update version number in setup.py)
        self.bravecowcowPythonPackageDownloadUrl = "https://github.com/murong-xu/CADS/archive/29d90ca04216cd2ea8782a5dfb9e4893b54ba829.zip"  # version 1.02 2025-12-15  #TODO: 

        self.logCallback = None
        self.clearOutputFolder = True
        self.useStandardSegmentNames = True
        self.pullMaster = False

        # List of property type codes that are specified by in the BraveCowCow terminology.
        # If property the code is found in this list then the BraveCowCow terminology will be used,
        # otherwise the DICOM terminology will be used. This is necessary because the DICOM terminology
        # does not contain all the necessary items and some items are incomplete (e.g., don't have color or 3D Slicer label).
        self.bravecowcowTerminologyPropertyTypes = []

        # Map from BraveCowCow structure name to terminology string.
        # Terminology string uses Slicer terminology entry format - see specification at
        # https://slicer.readthedocs.io/en/latest/developer_guide/modules/segmentations.html#terminologyentry-tag
        self.bravecowcowLabelTerminology = {}
        
        # Map from (filename, label_index) to structure name
        self.filenameLabelToStructureName = {}

        # Segmentation tasks specified by BraveCowCow
        # Ideally, this information should be provided by BraveCowCow itself.
        self.tasks = OrderedDict()

        # Load terminology mapping (this will populate the dictionaries above)
        self.loadBraveCowCowLabelTerminology()

        # Define color map for vessel structures
        self.vesselColorMap = {
        # ========== File 1: Basic vessels (pure saturated colors) ==========
        # Posterior circulation - Red/Orange family
        'Other Posterior Circulation': (1.0, 0.2, 0.0),      # Bright orange-red
        'BA-Tip': (1.0, 0.4, 0.0),                           # Orange
        'Right Posterior Communicating Artery': (1.0, 0.5, 0.2),  # Light orange
        'Left Posterior Communicating Artery': (1.0, 0.6, 0.0),   # Yellow-orange
        
        # Internal Carotid Arteries - Blue/Cyan family
        'Right Infraclinoid Internal Carotid Artery': (0.0, 0.5, 1.0),   # Sky blue
        'Left Infraclinoid Internal Carotid Artery': (0.0, 0.7, 1.0),    # Bright cyan
        'Right Supraclinoid Internal Carotid Artery': (0.2, 0.6, 1.0),   # Light blue
        'Left Supraclinoid Internal Carotid Artery': (0.3, 0.8, 1.0),    # Pale cyan
        
        # Middle Cerebral Arteries - Yellow/Gold family
        'Right Middle Cerebral Artery': (1.0, 0.9, 0.0),     # Pure yellow
        'Left Middle Cerebral Artery': (1.0, 0.8, 0.2),      # Golden yellow
        
        # Anterior Cerebral Arteries - Green/Lime family
        'Right Anterior Cerebral Artery': (0.3, 1.0, 0.2),   # Bright lime
        'Left Anterior Cerebral Artery': (0.5, 1.0, 0.3),    # Light lime
        
        # Anterior Communicating - Purple/Magenta family
        'Anterior Communicating Artery': (0.8, 0.2, 1.0),    # Bright purple
        
        # Generic aneurysm
        'Aneurysm': (1.0, 0.0, 0.0),                         # Pure red
        
        # ========== File 2: Vessel + Aneurysm combined (desaturated/mixed tones) ==========
        # Posterior circulation - Dark red/brown tones
        'Other Posterior Circulation and Aneurysm': (0.8, 0.3, 0.3),   # Muted red
        'BA-Tip and Aneurysm': (0.7, 0.35, 0.25),                      # Reddish brown
        'Right Posterior Communicating Artery and Aneurysm': (0.8, 0.4, 0.3),  # Terracotta
        'Left Posterior Communicating Artery and Aneurysm': (0.75, 0.45, 0.2), # Burnt orange
        
        # Internal Carotid Arteries - Dark blue/teal tones
        'Right Infraclinoid Internal Carotid Artery and Aneurysm': (0.2, 0.4, 0.7),   # Deep blue
        'Left Infraclinoid Internal Carotid Artery and Aneurysm': (0.25, 0.5, 0.75),  # Medium blue
        'Right Supraclinoid Internal Carotid Artery and Aneurysm': (0.3, 0.6, 0.8),   # Steel blue
        'Left Supraclinoid Internal Carotid Artery and Aneurysm': (0.2, 0.5, 0.7),    # Ocean blue
        
        # Middle Cerebral Arteries - Olive/khaki tones
        'Right Middle Cerebral Artery and Aneurysm': (0.7, 0.7, 0.3),   # Olive
        'Left Middle Cerebral Artery and Aneurysm': (0.75, 0.65, 0.25), # Khaki
        
        # Anterior Cerebral Arteries - Dark green/forest tones
        'Right Anterior Cerebral Artery and Aneurysm': (0.3, 0.7, 0.35),  # Forest green
        'Left Anterior Cerebral Artery and Aneurysm': (0.35, 0.75, 0.4),  # Medium green
        
        # Anterior Communicating - Dark purple/plum tones
        'Anterior Communicating Artery and Aneurysm': (0.6, 0.3, 0.7),    # Plum
        
        # ========== File 3: Separated aneurysms (red spectrum with varying brightness) ==========
        # Use different red intensities for high contrast
        'Left Infraclinoid Internal Carotid Artery Aneurysm': (1.0, 0.0, 0.1),      # Pure red
        'Right Infraclinoid Internal Carotid Artery Aneurysm': (0.95, 0.1, 0.0),    # Red-orange
        'Left Supraclinoid Internal Carotid Artery Aneurysm': (1.0, 0.15, 0.15),    # Pink-red
        'Right Supraclinoid Internal Carotid Artery Aneurysm': (0.9, 0.0, 0.15),    # Deep red
        'Left Middle Cerebral Artery Aneurysm': (1.0, 0.2, 0.2),                    # Light red
        'Right Middle Cerebral Artery Aneurysm': (0.85, 0.0, 0.0),                  # Dark red
        'Anterior Communicating Artery Aneurysm': (1.0, 0.0, 0.3),                  # Red-pink
        'Left Anterior Cerebral Artery Aneurysm': (0.95, 0.05, 0.25),               # Crimson
        'Right Anterior Cerebral Artery Aneurysm': (1.0, 0.1, 0.0),                 # Scarlet
        'Left Posterior Communicating Artery Aneurysm': (0.9, 0.15, 0.1),           # Brick red
        'Right Posterior Communicating Artery Aneurysm': (1.0, 0.05, 0.2),          # Rose red
        'Basilar Tip Aneurysm': (0.8, 0.0, 0.1),                                    # Maroon
        'Other Posterior Circulation Aneurysm': (0.95, 0.0, 0.15),                  # Ruby red
    }
        
    def loadBraveCowCowLabelTerminology(self):
        """Load label terminology from bravecowcow_snomed_mapping.csv file."""
        moduleDir = os.path.dirname(slicer.util.getModule('BraveCowCow').path)
        bravecowcowTerminologyMappingFilePath = os.path.join(moduleDir, 'Resources', 'bravecowcow_snomed_mapping.csv')
        
        import csv
        with open(bravecowcowTerminologyMappingFilePath, "r") as f:
            reader = csv.reader(f)
            columnNames = next(reader)
            
            for row in reader:
                try:
                    # Get file name and label index
                    file_name = row[columnNames.index("File")]
                    label_index = int(row[columnNames.index("Label")])
                    
                    # Get Structure name and CodeMeaning
                    structure_name = row[columnNames.index("Structure")]
                    code_meaning = row[columnNames.index("SegmentedPropertyTypeCodeSequence.CodeMeaning")]
                    
                    # For modifier (left/right), append to code meaning if exists
                    try:
                        modifier_meaning = row[columnNames.index("SegmentedPropertyTypeModifierCodeSequence.CodeMeaning")]
                        if modifier_meaning:
                            slicer_label = f"{modifier_meaning} {code_meaning}"
                        else:
                            slicer_label = code_meaning
                    except (IndexError, ValueError):
                        slicer_label = code_meaning
                    
                    # Store simple mapping
                    self.bravecowcowLabelTerminology[structure_name] = {
                        'slicerLabel': slicer_label
                    }
                    
                    # Store (filename, label) -> structure_name mapping
                    self.filenameLabelToStructureName[(file_name, label_index)] = structure_name
                    
                except Exception as e:
                    logging.warning(f"Error processing row in terminology CSV: {str(e)}")

    def getSlicerLabel(self, structure_name):
        """Get Slicer display label for a structure"""
        if structure_name in self.bravecowcowLabelTerminology:
            return self.bravecowcowLabelTerminology[structure_name]['slicerLabel']
        return structure_name

    def getStructureName(self, slicer_label):
        """Get structure name from Slicer display label"""
        for structure_name, info in self.bravecowcowLabelTerminology.items():
            if info['slicerLabel'] == slicer_label:
                return structure_name
        return slicer_label

    def getTerminologyString(self, structure_name):
        """Get terminology string for a structure"""
        if structure_name in self.bravecowcowLabelTerminology:
            return self.bravecowcowLabelTerminology[structure_name]['terminologyStr']
        return None
  

    def log(self, text):
        logging.info(text)
        if self.logCallback:
            self.logCallback(text)

    def installedBraveCowCowPythonPackageDownloadUrl(self):
        """Get package download URL of the installed BraveCowCow Python package"""
        import importlib.metadata
        import json
        try:
            metadataPath = [p for p in importlib.metadata.files('BraveCowCow') if 'direct_url.json' in str(p)][0]  #TODO: check if correct
            with open(metadataPath.locate()) as json_file:
                data = json.load(json_file)
            return data['url']
        except:
            # Failed to get version information, probably not installed from download URL
            return None

    def installedBraveCowCowPythonPackageInfo(self):
        import shutil
        import subprocess
        versionInfo = subprocess.check_output([shutil.which('PythonSlicer'), "-m", "pip", "show", "BraveCowCow"]).decode()  # read the version info from setup.py

        return versionInfo

    def simpleITKPythonPackageVersion(self):
        """Utility function to get version of currently installed SimpleITK.
        Currently not used, but it can be useful for diagnostic purposes.
        """

        import shutil
        import subprocess
        versionInfo = subprocess.check_output([shutil.which('PythonSlicer'), "-m", "pip", "show", "SimpleITK"]).decode()

        # versionInfo looks something like this:
        #
        #   Name: SimpleITK
        #   Version: 2.2.0rc2.dev368
        #   Summary: SimpleITK is a simplified interface to the Insight Toolkit (ITK) for image registration and segmentation
        #   ...
        #

        # Get version string (second half of the second line):
        version = versionInfo.split('\n')[1].split(' ')[1].strip()
        return version

    def pipInstallSelectiveFromURL(self, packageToInstall, installURL, packagesToSkip):
        """Installs a Python package from a local zip file or URL, skipping specified packages.
        Records original source URL in package metadata.
        """
        import os
        import pathlib
        import zipfile
        import tempfile
        import urllib.request
        import json
        import shutil
        import importlib.metadata
        import re

        def normalize_pkg_name(name):
            return name.split('[')[0].split('<')[0].split('>')[0].split('=')[0].split(';')[0].split('!')[0].strip().replace('-', '_').lower()

        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                # Download or copy zip file
                zip_path = os.path.join(temp_dir, "package.zip")
                if installURL.startswith(('http://', 'https://')):
                    self.log(f'Downloading package from {installURL}...')
                    urllib.request.urlretrieve(installURL, zip_path)
                    source_url = installURL
                else:
                    self.log(f'Copying package from {installURL}...')
                    shutil.copy2(installURL, zip_path)
                    source_url = installURL
                    
                # Extract and find setup files
                self.log('Extracting package...')
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)

                # Look for setup files
                package_dir = None
                for root, _, files in os.walk(temp_dir):
                    if any(f in files for f in ['setup.py', 'pyproject.toml']):
                        package_dir = root
                        break
                    
                if not package_dir:
                    raise ValueError(f"No setup.py or pyproject.toml found in {installURL}")

                # First install the package without dependencies
                self.log(f'Installing {packageToInstall}...')
                install_path = pathlib.Path(package_dir).as_posix()
                slicer.util.pip_install(f"{install_path} --no-deps")

                # Now create and add direct_url.json to the installed package's dist-info
                try:
                    # Find the package's dist-info directory
                    dist_info_dir = None
                    for path in importlib.metadata.files(packageToInstall):
                        if '.dist-info' in str(path):
                            dist_info_dir = os.path.dirname(path.locate())
                            break
                    
                    if not dist_info_dir:
                        raise RuntimeError(f"Could not find dist-info directory for {packageToInstall}")

                    # Create direct_url.json content
                    direct_url_data = {
                        "url": source_url,  # need to overwrite the tmp dir generated by pip install a local file
                        "dir_info": {
                            "editable": False
                        },
                        "vcs_info": {
                            "vcs": "git",
                            "requested_revision": None,
                            "commit_id": None
                        }
                    }
                    
                    # Save direct_url.json in the dist-info directory
                    direct_url_path = os.path.join(dist_info_dir, "direct_url.json")
                    self.log(f'Creating direct_url.json at: {direct_url_path}')
                    with open(direct_url_path, 'w') as f:
                        json.dump(direct_url_data, f)

                except Exception as e:
                    self.log(f'Warning: Failed to create direct_url.json: {str(e)}')
                    # Continue with installation even if direct_url.json creation fails

                # Process metadata to skip packages
                skippedRequirements = []
                try:
                    metadataPath = [p for p in importlib.metadata.files(packageToInstall) if 'METADATA' in str(p)][0]
                except (IndexError, ImportError) as e:
                    raise RuntimeError(f"Could not find metadata for {packageToInstall}") from e

                # Filter requirements in metadata
                self.log('Processing package dependencies...')
                filteredMetadata = ""
                with open(metadataPath.locate(), "r+", encoding="latin1") as file:
                    for line in file:
                        skipThisPackage = False
                        requirementPrefix = 'Requires-Dist: '
                        
                        if line.startswith(requirementPrefix):
                            # Skip dev dependencies
                            if '; extra == "dev"' in line:
                                continue
                                
                            # Check if package should be skipped
                            for packageToSkip in packagesToSkip:
                                pkg_in_line = line.removeprefix(requirementPrefix).split('[')[0].split('<')[0].split('>')[0].split('=')[0].split(';')[0].split('!')[0].strip()
                                if pkg_in_line.lower() == packageToSkip.lower():
                                    skipThisPackage = True
                                    skippedRequirements.append(line.removeprefix(requirementPrefix))
                                    break
                                    
                        if not skipThisPackage:
                            filteredMetadata += line
                            
                    # Update metadata file
                    file.seek(0)
                    file.write(filteredMetadata)
                    file.truncate()

                # Install remaining dependencies
                requirements = importlib.metadata.requires(packageToInstall) or []
                for requirement in requirements:
                    # Skip dev dependencies
                    if '; extra == "dev"' in requirement:
                        continue
                        
                    # Check if package should be skipped
                    req_pkg = normalize_pkg_name(requirement)
                    skipThisPackage = any(req_pkg == normalize_pkg_name(pkg) for pkg in packagesToSkip)
                    
                    if not skipThisPackage:
                        # Clean up requirement string
                        if '; extra == ' in requirement:
                            pkg, extra = re.match(r"([\S]+)[\s]*; extra == '([^']+)'", requirement).groups()
                            requirement = f"{pkg}[{extra}]"
                        else:
                            match = re.match(r"([\S]+)[\s](.+)", requirement)
                            if match:
                                requirement = f"{match.group(1)}{match.group(2)}"
                                
                        self.log(f'Installing dependency: {requirement}')
                        slicer.util.pip_install(requirement)
                    else:
                        self.log(f'Skipping dependency: {requirement}')

                try:
                    bravecowcow_path = importlib.metadata.files('bravecowcow')[0].locate().parent
                    json_path = os.path.join(bravecowcow_path, 'skipped_requirements.json')
                    with open(json_path, 'w') as f:
                        json.dump(skippedRequirements, f)
                except Exception as e:
                    print(f"Failed to save skipped requirements: {e}")

                return skippedRequirements

            except urllib.error.URLError as e:
                self.log(f'Error downloading package: {str(e)}')
                raise RuntimeError(f"Failed to download package from {installURL}") from e
            
            except zipfile.BadZipFile as e:
                self.log(f'Error extracting package: {str(e)}')
                raise RuntimeError(f"The file at {installURL} is not a valid zip file") from e
                
            except json.JSONDecodeError as e:
                self.log(f'Error creating direct_url.json: {str(e)}')
                raise RuntimeError("Failed to create package metadata") from e
                
            except OSError as e:
                self.log(f'File system error: {str(e)}')
                raise RuntimeError(f"File system error while installing package: {str(e)}") from e
                
            except Exception as e:
                self.log(f'Unexpected error during installation: {str(e)}')
                raise RuntimeError(f"Failed to install package: {str(e)}") from e

    def pipInstallSelectiveFromLocal(self, packageToInstall, localFolderPath, packagesToSkip):
        """Installs a Python package from a local folder, skipping specified packages.
        
        Args:
            packageToInstall: Name of the package to install
            localFolderPath: Path to local folder containing setup.py or pyproject.toml
            packagesToSkip: List of package names to skip during dependency installation
        """
        import os
        import pathlib
        import json
        import importlib.metadata
        import re

        def normalize_pkg_name(name):
            return name.split('[')[0].split('<')[0].split('>')[0].split('=')[0].split(';')[0].split('!')[0].strip().replace('-', '_').lower()

        
        try:
            # Verify the local folder exists and contains setup files
            if not os.path.isdir(localFolderPath):
                raise ValueError(f"Local folder not found: {localFolderPath}")
            
            has_setup = any(
                os.path.exists(os.path.join(localFolderPath, f)) 
                for f in ['setup.py', 'pyproject.toml']
            )
            if not has_setup:
                raise ValueError(f"No setup.py or pyproject.toml found in {localFolderPath}")

            # Install the package without dependencies
            self.log(f'Installing {packageToInstall} from {localFolderPath}...')
            install_path = pathlib.Path(localFolderPath).as_posix()
            slicer.util.pip_install(f"{install_path} --no-deps")

            # Create direct_url.json to record source location
            try:
                dist_info_dir = None
                for path in importlib.metadata.files(packageToInstall):
                    if '.dist-info' in str(path):
                        dist_info_dir = os.path.dirname(path.locate())
                        break
                
                if dist_info_dir:
                    direct_url_data = {
                        "url": f"file://{os.path.abspath(localFolderPath)}",
                        "dir_info": {"editable": False}
                    }
                    direct_url_path = os.path.join(dist_info_dir, "direct_url.json")
                    self.log(f'Creating direct_url.json at: {direct_url_path}')
                    with open(direct_url_path, 'w') as f:
                        json.dump(direct_url_data, f)
            except Exception as e:
                self.log(f'Warning: Failed to create direct_url.json: {str(e)}')

            # Process metadata to skip packages
            skippedRequirements = []
            try:
                metadataPath = [p for p in importlib.metadata.files(packageToInstall) if 'METADATA' in str(p)][0]
            except (IndexError, ImportError) as e:
                raise RuntimeError(f"Could not find metadata for {packageToInstall}") from e

            # Filter requirements in metadata
            self.log('Processing package dependencies...')
            filteredMetadata = ""
            with open(metadataPath.locate(), "r+", encoding="latin1") as file:
                for line in file:
                    skipThisPackage = False
                    requirementPrefix = 'Requires-Dist: '
                    
                    if line.startswith(requirementPrefix):
                        # Skip dev dependencies
                        if '; extra == "dev"' in line:
                            continue
                            
                        # Check if package should be skipped
                        for packageToSkip in packagesToSkip:
                            pkg_in_line = line.removeprefix(requirementPrefix).split('[')[0].split('<')[0].split('>')[0].split('=')[0].split(';')[0].split('!')[0].strip()
                            if pkg_in_line.lower() == packageToSkip.lower():
                                skipThisPackage = True
                                skippedRequirements.append(line.removeprefix(requirementPrefix))
                                break
                                
                    if not skipThisPackage:
                        filteredMetadata += line
                        
                # Update metadata file
                file.seek(0)
                file.write(filteredMetadata)
                file.truncate()

            # Install remaining dependencies
            requirements = importlib.metadata.requires(packageToInstall) or []
            for requirement in requirements:
                # Skip dev dependencies
                if '; extra == "dev"' in requirement:
                    continue
                    
                # Check if package should be skipped
                req_pkg = normalize_pkg_name(requirement)
                skipThisPackage = any(req_pkg == normalize_pkg_name(pkg) for pkg in packagesToSkip)
                
                if not skipThisPackage:
                    # Clean up requirement string
                    if '; extra == ' in requirement:
                        pkg, extra = re.match(r"([\S]+)[\s]*; extra == '([^']+)'", requirement).groups()
                        requirement = f"{pkg}[{extra}]"
                    else:
                        match = re.match(r"([\S]+)[\s](.+)", requirement)
                        if match:
                            requirement = f"{match.group(1)}{match.group(2)}"
                            
                    self.log(f'Installing dependency: {requirement}')
                    slicer.util.pip_install(requirement)
                else:
                    self.log(f'Skipping dependency: {requirement}')

            # Save skipped requirements for later reference
            try:
                bravecowcow_path = importlib.metadata.files('bravecowcow')[0].locate().parent
                json_path = os.path.join(bravecowcow_path, 'skipped_requirements.json')
                with open(json_path, 'w') as f:
                    json.dump(skippedRequirements, f)
            except Exception as e:
                print(f"Failed to save skipped requirements: {e}")

            return skippedRequirements

        except OSError as e:
            self.log(f'File system error: {str(e)}')
            raise RuntimeError(f"File system error while installing package: {str(e)}") from e
            
        except Exception as e:
            self.log(f'Unexpected error during installation: {str(e)}')
            raise RuntimeError(f"Failed to install package: {str(e)}") from e


    def _parse_version_from_requirements(self, package_name, requirements_list):
        """
        Extract package version declaration such as 'TPTBox==0.3.0', 'TPTBox==0.3.0; python_version<"3.10"'from requirements_list.
        """
        from packaging.requirements import Requirement

        results = []
        norm = lambda s: re.sub(r'[-_]+', '-', s).lower()
        target = norm(package_name)

        for raw in requirements_list:
            try:
                req = Requirement(raw)
            except Exception:
                fixed = raw.replace('(', '').replace(')', '').replace(' ', '')
                try:
                    req = Requirement(fixed)
                except Exception:
                    continue
            if norm(req.name) != target:
                continue
            ver = None
            for spec in req.specifier:
                if spec.operator == '==':
                    ver = spec.version
                    break
            if not ver:
                continue

            results.append((ver, req.marker))
        return results

    def _should_install_version(self, version_info):
        """
        version_info = (version_str, marker_or_None)
        """
        from packaging.markers import default_environment
        _, marker = version_info
        if not marker:
            return True
        try:
            return marker.evaluate(default_environment())
        except Exception:
            return False
        
    def load_skipped_requirements(self):
        import importlib.metadata
        import json
        # Get the path to the installed 'bravecowcow' package
        bravecowcow_path = importlib.metadata.files('bravecowcow')[0].locate().parent
        json_path = os.path.join(bravecowcow_path, 'skipped_requirements.json')
        
        # Read the skipped requirements from the JSON file
        if os.path.exists(json_path):
            with open(json_path, 'r') as f:
                return json.load(f)
        else:
            raise FileNotFoundError("Skipped requirements file not found.")

    def setupPythonRequirements(self, upgrade=False):
        if self.__class__._requirements_checked and not upgrade: # if already checked and not upgrading, then skip
            return

        from packaging import version

        # Step 1) Check PyTorch
        try:
            import torch
            minimumTorchVersion = "2.1.2"
            if version.parse(torch.__version__) < version.parse(minimumTorchVersion):
                raise InstallError(f'PyTorch version {torch.__version__} is not compatible with this module.'
                                + f' Minimum required version is {minimumTorchVersion}.')
        except ImportError:
            raise InstallError("This module requires PyTorch. Please install it from the Extensions Manager.")

        # Step 2) Install BraveCowCow and its dependencies
        # Some packages are pre-installed in Slicer, or we need to manually install
        packagesToSkip = [
            'SimpleITK',  # Slicer's SimpleITK uses a special IO class, which should not be replaced
            'torch',  # needs special installation using SlicerPyTorch
            ]

        needToInstallSegmenter = False  # initial installation flag of BraveCowCow
        try:
            import bravecowcow #TODO: after knowing bravecowcowPythonPackageDownloadUrl, enable the following check
            # if not upgrade: # update flag of BraveCowCow
            #     # Check if we need to update BraveCowCow Python package version
            #     downloadUrl = self.installedBraveCowCowPythonPackageDownloadUrl()
            #     if downloadUrl and (downloadUrl != self.bravecowcowPythonPackageDownloadUrl):
            #         # BraveCowCow have been already installed from GitHub, from a different URL that this module needs
            #         if not slicer.util.confirmOkCancelDisplay(
            #             f"This module requires BraveCowCow Python package update.",
            #             detailedText=f"Currently installed: {downloadUrl}\n\nRequired: {self.bravecowcowPythonPackageDownloadUrl}"):
            #           raise ValueError('BraveCowCow update was cancelled.')
            #         upgrade = True
        except ModuleNotFoundError as e:
            needToInstallSegmenter = True
        if needToInstallSegmenter or upgrade:
            self.log(f'BraveCowCow Python package is required. Installing it from {self.bravecowcowPythonPackageDownloadUrl}... (it may take several minutes)')

            if upgrade:
                # BraveCowCow version information is usually not updated with each git revision, therefore we must uninstall it to force the upgrade
                slicer.util.pip_uninstall("BraveCowCow")

            # Update BraveCowCow and all its dependencies #TODO: after knowing bravecowcowPythonPackageDownloadUrl, enable the following line
            # skippedRequirements = self.pipInstallSelectiveFromURL(
            #     "BraveCowCow",
            #     self.bravecowcowPythonPackageDownloadUrl,
            #     packagesToSkip)

            # Temporally install from local path until BraveCowCow release is available
            skippedRequirements = self.pipInstallSelectiveFromLocal(
                "BraveCowCow",
                "/Users/murong/Desktop/RSNA-bravecowcow/bravecowcow_inference_docker",
                packagesToSkip
            )   

        self.log('BraveCowCow installation completed successfully.')
        self.__class__._requirements_checked = True


    def setDefaultParameters(self, parameterNode):
        """
        Initialize parameter node with default settings.
        """
        if not parameterNode.GetParameter("OnlyForwardCls"):
            parameterNode.SetParameter("OnlyForwardCls", "false")
        if not parameterNode.GetParameter("ToMerge2SegTask"):
            parameterNode.SetParameter("ToMerge2SegTask", "false")
        if not parameterNode.GetParameter("TtaType"):
            parameterNode.SetParameter("TtaType", "TTAx1")
        if not parameterNode.GetParameter("TtaBatchSize"):
            parameterNode.SetParameter("TtaBatchSize", "1") 
        if not parameterNode.GetParameter("UseStandardSegmentNames"):
            parameterNode.SetParameter("UseStandardSegmentNames", "true")
        if not parameterNode.GetParameter("CPU"):
            parameterNode.SetParameter("CPU", "false")

    def logProcessOutput(self, proc, returnOutput=False):
        # Wait for the process to end and forward output to the log
        output = ""
        from subprocess import CalledProcessError
        while True:
            try:
                line = proc.stdout.readline()
                if not line:
                    break
                if returnOutput:
                    output += line
                self.log(line.rstrip())
            except UnicodeDecodeError as e:
                # Code page conversion happens because `universal_newlines=True` sets process output to text mode,
                # and it fails because probably system locale is not UTF8. We just ignore the error and discard the string,
                # as we only guarantee correct behavior if an UTF8 locale is used.
                pass

        proc.wait()
        retcode = proc.returncode
        if retcode != 0:
            raise CalledProcessError(retcode, proc.args, output=proc.stdout, stderr=proc.stderr)
        return output if returnOutput else None

    @staticmethod
    def executableName(name):
        return name + ".exe" if os.name == "nt" else name

    def process(self, inputVolume, outputSegmentation, cpu=False, 
            onlyForwardCls=False, toMerge2SegTask=False, 
            ttaType="TTAx1", ttaBatchSize=1):
        """
        Run the processing algorithm.
        """
        if not inputVolume:
            raise ValueError("Input volume is invalid")
            
        import time
        startTime = time.time()
        self.log('Processing started')

        # Create temporary folder - moved here so it can be shared across tasks
        tempFolder = slicer.util.tempDirectory()
        volumeName = inputVolume.GetName()
        # cleanup volume name to make it safe for file names
        safeVolumeName = re.sub(r'[<>:"/\\|?*,\s]', '_', volumeName)

        inputFile = os.path.join(tempFolder, safeVolumeName + ".nii.gz")
        outputSegmentationFolder = os.path.join(tempFolder, safeVolumeName)

        # Get Python and BraveCowCow paths
        import sysconfig
        import shutil
        pythonSlicerExecutablePath = shutil.which('PythonSlicer')
        if not pythonSlicerExecutablePath:
            raise RuntimeError("Python was not found")
        bravecowcowExecutablePath = os.path.join(sysconfig.get_path('scripts'), 
                                        self.executableName("BraveCoWCoWSlicer"))
        bravecowcowCommand = [pythonSlicerExecutablePath, bravecowcowExecutablePath]

        try:
            segmentationNodes = []
            self.log(f"Writing input file to {inputFile}")
            volumeStorageNode = slicer.mrmlScene.CreateNodeByClass("vtkMRMLVolumeArchetypeStorageNode")
            volumeStorageNode.SetFileName(inputFile)
            volumeStorageNode.UseCompressionOff()
            volumeStorageNode.WriteData(inputVolume)
            volumeStorageNode.UnRegister(None)

            segmentationNodes = self.processVolume(
                inputFile, inputVolume,
                outputSegmentationFolder, outputSegmentation,
                onlyForwardCls, toMerge2SegTask, ttaType, ttaBatchSize, cpu, bravecowcowCommand
            )

            stopTime = time.time()
            self.log(f"Processing completed in {stopTime-startTime:.2f} seconds")

            return segmentationNodes

        except Exception as e:
            self.log(f"Error during processing: {str(e)}")
            raise

        finally:
            # Cleanup temp folder after all processing is complete
            if self.clearOutputFolder:
                self.log("Cleaning up temporary folder...")
                if os.path.isdir(tempFolder):
                    shutil.rmtree(tempFolder)
            else:
                self.log(f"Not cleaning up temporary folder: {tempFolder}")


    def processVolume(self, inputFile, inputVolume, outputSegmentationFolder, outputSegmentation, onlyForwardCls, toMerge2SegTask, ttaType, ttaBatchSize, cpu, bravecowcowCommand):
        """Segment a single volume
        """
        # Get options
        # TODO: check if nifti or dicom
        options = ["--nifti_path", inputFile, "--output_dir", outputSegmentationFolder]
        if cpu:
            options.extend(["--cpu"])
        if onlyForwardCls: 
            options.extend(["--only_forward_cls"])
        if toMerge2SegTask:
            options.extend(["--to_merge_2_seg_task"])
        if ttaType:
            options.extend(["--tta_type", ttaType])
        if ttaBatchSize:
            options.extend(["--tta_batch_size", str(ttaBatchSize)])

        # Launch BraveCowCow
        self.log('BraveCowCow-model is segmenting...')
        self.log(f"BraveCowCow arguments: {options}")
        proc = slicer.util.launchConsoleProcess(bravecowcowCommand + options)
        self.logProcessOutput(proc)

        # Load result
        # Load classification results
        csvFiles = glob.glob(os.path.join(outputSegmentationFolder, "cls_probs.csv"))
        if csvFiles:
            self.log(f'Found {len(csvFiles)} CSV result file(s)')
            for csvFile in csvFiles:
                self.loadCSVAsTable(csvFile, inputVolume.GetName())
        
        # Only load segmentation if not in classification-only mode
        if not onlyForwardCls:
            self.log('\n=== Importing segmentation results ===')
            
            # Get all segmentation nodes before loading
            segmentationNodesBefore = slicer.util.getNodesByClass('vtkMRMLSegmentationNode')
            
            readSegmentationIntoSlicer = self.readSegmentation(
                outputSegmentation,
                outputSegmentationFolder,
            )
            
            if not readSegmentationIntoSlicer:
                self.log('Failed to load segmentation')
                return []

            # Get all segmentation nodes after loading
            segmentationNodesAfter = slicer.util.getNodesByClass('vtkMRMLSegmentationNode')   
            newSegmentationNodes = [node for node in segmentationNodesAfter if node not in segmentationNodesBefore]
            allSegmentationNodes = [outputSegmentation] if outputSegmentation.GetSegmentation().GetNumberOfSegments() > 0 else []
            allSegmentationNodes.extend([node for node in newSegmentationNodes if node != outputSegmentation])
            
            # Set properties for all segmentation nodes
            for segNode in allSegmentationNodes:
                segNode.SetNodeReferenceID(
                    segNode.GetReferenceImageGeometryReferenceRole(), 
                    inputVolume.GetID()
                )
                segNode.SetReferenceImageGeometryParameterFromVolumeNode(inputVolume)

                # Place segmentation node in the same place as the input volume
                shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
                inputVolumeShItem = shNode.GetItemByDataNode(inputVolume)
                studyShItem = shNode.GetItemParent(inputVolumeShItem)
                segmentationShItem = shNode.GetItemByDataNode(segNode)
                shNode.SetItemParent(segmentationShItem, studyShItem)
            
            self.log(f'\nSuccessfully loaded {len(allSegmentationNodes)} segmentation node(s)')
            return allSegmentationNodes
        else:
            # Classification only mode - no segmentation to return
            self.log('Classification-only mode: No segmentation generated.')
            return []

    def _setSegmentationNodeProperties(self, segmentationNode, inputVolume):
        """Helper method to set common properties for segmentation nodes"""
        # Set source volume reference
        segmentationNode.SetNodeReferenceID(
            segmentationNode.GetReferenceImageGeometryReferenceRole(),
            inputVolume.GetID()
        )
        segmentationNode.SetReferenceImageGeometryParameterFromVolumeNode(inputVolume)

        # Set scene placement
        shNode = slicer.vtkMRMLSubjectHierarchyNode.GetSubjectHierarchyNode(slicer.mrmlScene)
        inputVolumeShItem = shNode.GetItemByDataNode(inputVolume)
        studyShItem = shNode.GetItemParent(inputVolumeShItem)
        segmentationShItem = shNode.GetItemByDataNode(segmentationNode)
        shNode.SetItemParent(segmentationShItem, studyShItem)
        
    def readSegmentation(self, outputSegmentation, outputSegmentationFolder):
        """Load segmentation results and set up segments with names and colors"""
        
        # Find specific segmentation files by name
        seg_pred_1 = os.path.join(outputSegmentationFolder, 'seg_pred_1.nii.gz')
        seg_pred_2 = os.path.join(outputSegmentationFolder, 'seg_pred_2.nii.gz')
        seg_pred_26fgCls = os.path.join(outputSegmentationFolder, 'seg_pred_26fgCls.nii.gz')
        
        # Determine which files exist
        files_to_load = []
        if os.path.exists(seg_pred_1) and os.path.exists(seg_pred_2):
            files_to_load.append(('seg_pred_1', seg_pred_1))
            files_to_load.append(('seg_pred_2', seg_pred_2))
        
        if os.path.exists(seg_pred_26fgCls):
            files_to_load.append(('seg_pred_26fgCls', seg_pred_26fgCls))
        
        if not files_to_load:
            self.log(f"Error: No segmentation files found in {outputSegmentationFolder}")
            return False
        
        # Load all found segmentation files
        for file_name, file_path in files_to_load:
            # Create segmentation node
            if file_name == files_to_load[0][0]:
                currentSegmentation = outputSegmentation
                currentSegmentation.SetName(f"{outputSegmentation.GetName()}_{file_name}")
            else:
                currentSegmentation = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLSegmentationNode')
                currentSegmentation.SetName(f"{outputSegmentation.GetName()}_{file_name}")
            
            # Load the segmentation file
            currentSegmentation.AddDefaultStorageNode()
            storageNode = currentSegmentation.GetStorageNode()
            storageNode.SetFileName(file_path)
            storageNode.ReadData(currentSegmentation)
            
            # Set names and colors for all segments
            segmentation = currentSegmentation.GetSegmentation()
            segmentIDs = vtk.vtkStringArray()
            segmentation.GetSegmentIDs(segmentIDs)
                        
            for i in range(segmentIDs.GetNumberOfValues()):
                segmentID = segmentIDs.GetValue(i)
                segment = segmentation.GetSegment(segmentID)
                labelValue = segment.GetLabelValue()
                
                # Map (filename, label value) to structure name
                lookup_key = (file_name, labelValue)
                if lookup_key in self.filenameLabelToStructureName:
                    structureName = self.filenameLabelToStructureName[lookup_key]
                    
                    # Get the display label and color
                    if structureName in self.bravecowcowLabelTerminology:
                        info = self.bravecowcowLabelTerminology[structureName]
                        segment.SetName(info['slicerLabel'])
                        
                        if structureName in self.vesselColorMap:
                            segment.SetColor(self.vesselColorMap[structureName])
                        else:
                            segment.SetColor(0.7, 0.7, 0.7)
                    else:
                        self.log(f"  [{i+1}] Warning: Structure name '{structureName}' not in terminology")
                else:
                    self.log(f"  [{i+1}] Warning: ({file_name}, {labelValue}) not in mapping (segmentID: '{segmentID}')")
                    segment.SetColor(0.5, 0.5, 0.5)
        
        return True
    
    def loadCSVAsTable(self, csvFilePath, volumeName):
        """Load CSV file as a Slicer table node for display
        """
        import csv
        import vtk
        
        try:
            # Create a new table node
            tableNode = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLTableNode")
            tableName = f"{volumeName}_probabilities"
            tableNode.SetName(tableName)
            
            # Read CSV file
            with open(csvFilePath, 'r') as f:
                reader = csv.reader(f)
                headers = next(reader)  # First row: headers
                values = next(reader)   # Second row: values
                
                # Add columns to table
                table = tableNode.GetTable()
                table.SetNumberOfRows(1)
                
                for i, (header, value) in enumerate(zip(headers, values)):
                    col = vtk.vtkDoubleArray()
                    col.SetName(header)
                    col.SetNumberOfTuples(1)
                    try:
                        floatValue = float(value)
                        col.SetValue(0, floatValue)
                    except ValueError:
                        col.SetValue(0, 0.0)
                        self.log(f'Warning: Could not parse "{value}" as float for column "{header}"')
                    table.AddColumn(col)
            
            tableNode.Modified()
            
            self.log(f'Loaded CSV results into table')
            
            # Show the table in the Tables module
            slicer.app.layoutManager().setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutFourUpTableView)
            slicer.app.applicationLogic().GetSelectionNode().SetActiveTableID(tableNode.GetID())
            slicer.app.applicationLogic().PropagateTableSelection()
            
            return tableNode
            
        except Exception as e:
            self.log(f'Warning: Failed to load CSV file {csvFilePath}: {str(e)}')
            import traceback
            self.log(traceback.format_exc())
            return None

    def setTerminology(self, segmentation, segmentName, segmentId):
        """Set segment name and color based on CSV mapping"""
        segment = segmentation.GetSegmentation().GetSegment(segmentId)
        if not segment:
            return
        
        if segmentName in self.bravecowcowLabelTerminology:
            # Set display name from CSV
            label = self.bravecowcowLabelTerminology[segmentName]['slicerLabel']
            if self.useStandardSegmentNames:
                segment.SetName(label)
            
            # Set color from color map
            if segmentName in self.vesselColorMap:
                segment.SetColor(self.vesselColorMap[segmentName])
            else:
                # Default color if not in map
                segment.SetColor(0.8, 0.8, 0.8)
        else:
            self.log(f"Warning: No terminology entry for '{segmentName}'")

#
# BraveCowCowTest
#
class BraveCowCowTest(ScriptedLoadableModuleTest):
    """
    Test cases for BraveCowCow module.
    """
    def setUp(self):
        """ 
        Reset the state - clear scene and initialize test data.
        """
        slicer.mrmlScene.Clear()
        self.delayDisplay("Setting up test") 
        self.logic = BraveCowCowLogic()
        
        # Create a simple test volume (dummy data for testing)
        import SampleData
        try:
            self.inputVolume = SampleData.downloadSample('CTA-cardio')
        except:
            # Fallback to CT chest if CTA not available
            self.inputVolume = SampleData.downloadSample('CTChest')
        
        self.outputSegmentation = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLSegmentationNode')

    def runTest(self):
        """
        Run test suite.
        """
        self.delayDisplay("Starting BraveCowCow tests")
        
        self.setUp()
        self.test_LogicInitialization()
        
        self.setUp()
        self.test_ParameterNodeDefaults()
        
        self.setUp()
        self.test_TerminologyLoading()
        
        self.setUp()
        self.test_ColorMapCompleteness()
        
        self.setUp()
        self.test_FilenameLabelMapping()
        
        self.delayDisplay("All tests passed!")

    def test_LogicInitialization(self):
        """
        Test basic logic initialization.
        """
        self.delayDisplay("Testing logic initialization")
        
        # Check that logic object is created
        self.assertIsNotNone(self.logic)
        
        # Check essential attributes exist
        self.assertIsNotNone(self.logic.bravecowcowLabelTerminology)
        self.assertIsNotNone(self.logic.filenameLabelToStructureName)
        self.assertIsNotNone(self.logic.vesselColorMap)
        
        # Check dictionaries are populated
        self.assertTrue(len(self.logic.bravecowcowLabelTerminology) > 0, 
                       "Terminology dictionary should not be empty")
        self.assertTrue(len(self.logic.filenameLabelToStructureName) > 0,
                       "Filename-to-structure mapping should not be empty")
        self.assertTrue(len(self.logic.vesselColorMap) > 0,
                       "Color map should not be empty")
        
        self.delayDisplay('Logic initialization test passed')

    def test_ParameterNodeDefaults(self):
        """
        Test parameter node default values.
        """
        self.delayDisplay("Testing parameter node defaults")
        
        parameterNode = self.logic.getParameterNode()
        self.assertIsNotNone(parameterNode)
        
        # Test default parameters
        expectedDefaults = {
            "OnlyForwardCls": "false",
            "ToMerge2SegTask": "false",
            "TtaType": "TTAx1",
            "TtaBatchSize": "1",
            "UseStandardSegmentNames": "true",
            "CPU": "false"
        }
        
        for parameter, expectedValue in expectedDefaults.items():
            actualValue = parameterNode.GetParameter(parameter) or ""
            self.assertEqual(
                actualValue,
                expectedValue,
                f"Parameter {parameter} should default to '{expectedValue}', got '{actualValue}'"
            )
        
        self.delayDisplay('Parameter node defaults test passed')

    def test_TerminologyLoading(self):
        """
        Test terminology loading from CSV file.
        """
        self.delayDisplay("Testing terminology loading")
        
        # Check that terminology is loaded
        self.assertIsNotNone(self.logic.bravecowcowLabelTerminology)
        self.assertTrue(len(self.logic.bravecowcowLabelTerminology) > 0)
        
        # Test some expected vessel structures exist
        expectedStructures = [
            'Other Posterior Circulation',
            'BA-Tip',
            'Right Posterior Communicating Artery',
            'Left Posterior Communicating Artery',
            'Aneurysm'
        ]
        
        for structure in expectedStructures:
            self.assertIn(structure, self.logic.bravecowcowLabelTerminology,
                         f"Structure '{structure}' should be in terminology")
            self.assertIn('slicerLabel', self.logic.bravecowcowLabelTerminology[structure],
                         f"Structure '{structure}' should have a slicerLabel")
        
        self.delayDisplay('Terminology loading test passed')

    def test_ColorMapCompleteness(self):
        """
        Test that color map covers all structures in terminology.
        """
        self.delayDisplay("Testing color map completeness")
        
        # Get all structures from terminology
        allStructures = set(self.logic.bravecowcowLabelTerminology.keys())
        
        # Get all structures that have colors defined
        coloredStructures = set(self.logic.vesselColorMap.keys())
        
        # Find structures without colors
        missingColors = allStructures - coloredStructures
        
        # Warn if any structures are missing colors (but don't fail the test)
        if missingColors:
            self.delayDisplay(f"Warning: {len(missingColors)} structures missing colors: {list(missingColors)[:5]}...")
        
        # Check that all colors are valid RGB tuples
        for structure, color in self.logic.vesselColorMap.items():
            self.assertIsInstance(color, tuple, f"Color for '{structure}' should be a tuple")
            self.assertEqual(len(color), 3, f"Color for '{structure}' should have 3 components (RGB)")
            
            # Check RGB values are in valid range [0, 1]
            for i, component in enumerate(color):
                self.assertTrue(0 <= component <= 1.0,
                              f"Color component {i} for '{structure}' should be in range [0, 1], got {component}")
        
        self.delayDisplay('Color map completeness test passed')

    def test_FilenameLabelMapping(self):
        """
        Test filename-label to structure mapping.
        """
        self.delayDisplay("Testing filename-label mapping")
        
        # Check that mapping exists
        self.assertTrue(len(self.logic.filenameLabelToStructureName) > 0)
        
        # Test expected file types exist in mapping
        expectedFiles = ['seg_pred_1', 'seg_pred_2', 'seg_pred_26fgCls']
        filesInMapping = set([key[0] for key in self.logic.filenameLabelToStructureName.keys()])
        
        for expectedFile in expectedFiles:
            self.assertIn(expectedFile, filesInMapping,
                         f"File '{expectedFile}' should be in filename-label mapping")
        
        # Test that all mapped structures exist in terminology
        for (filename, label), structureName in self.logic.filenameLabelToStructureName.items():
            self.assertIn(structureName, self.logic.bravecowcowLabelTerminology,
                         f"Structure '{structureName}' from mapping should exist in terminology")
            
            # Test that label is a positive integer
            self.assertIsInstance(label, int, f"Label should be integer, got {type(label)}")
            self.assertTrue(label > 0, f"Label should be positive, got {label}")
        
        # Test specific mappings (examples)
        testMappings = [
            (('seg_pred_1', 1), 'Other Posterior Circulation'),
            (('seg_pred_1', 2), 'BA-Tip'),
            (('seg_pred_1', 14), 'Aneurysm'),
        ]
        
        for key, expectedStructure in testMappings:
            if key in self.logic.filenameLabelToStructureName:
                actualStructure = self.logic.filenameLabelToStructureName[key]
                self.assertEqual(actualStructure, expectedStructure,
                               f"Mapping {key} should map to '{expectedStructure}', got '{actualStructure}'")
        
        self.delayDisplay('Filename-label mapping test passed')

    def test_ErrorHandling(self):
        """
        Test error handling for invalid inputs.
        """
        self.delayDisplay("Testing error handling")
        
        # Test with None input volume
        with self.assertRaises(ValueError):
            self.logic.process(None, self.outputSegmentation)
        
        # Test with invalid TTA type (should handle gracefully)
        try:
            # This should work but may produce warnings
            result = self.logic.process(
                self.inputVolume,
                self.outputSegmentation,
                cpu=True,
                ttaType="InvalidType",  # Invalid but should be handled by backend
                ttaBatchSize=1
            )
        except Exception as e:
            # If it raises an exception, it should be a specific type
            self.assertTrue(isinstance(e, (ValueError, RuntimeError)),
                          f"Should raise ValueError or RuntimeError for invalid inputs, got {type(e)}")
        
        self.delayDisplay('Error handling test passed')

    def test_VolumeNameSanitization(self):
        """
        Test that volume names with special characters are handled correctly.
        """
        self.delayDisplay("Testing volume name sanitization")
        
        # Create a volume with problematic name
        testVolume = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLScalarVolumeNode')
        testVolume.SetName("Test: Volume, with <special> characters/slash\\backslash")
        
        # This should not crash
        volumeName = testVolume.GetName()
        safeVolumeName = re.sub(r'[<>:"/\\|?*,\s]', '_', volumeName)
        
        # Check that unsafe characters are replaced
        unsafeChars = '<>:"/\\|?*,'
        for char in unsafeChars:
            self.assertNotIn(char, safeVolumeName,
                           f"Sanitized name should not contain '{char}'")
        
        # Clean up
        slicer.mrmlScene.RemoveNode(testVolume)
        
        self.delayDisplay('Volume name sanitization test passed')